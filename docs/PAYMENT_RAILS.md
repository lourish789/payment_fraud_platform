# Payment rails: making every payment method reviewable

PayGuard started as a card-fraud system. This document covers its extension to **bank transfers,
mobile money and crypto exchange deposits/withdrawals**. It describes the research behind the design,
how commercial platforms solve the same problem, what was built, and what the evaluation found.
Numbers come from [EVALUATION.md](EVALUATION.md), which is regenerated from artifacts.

## 1. What "reviewable" has to mean

A payment method is reviewable when it can be **scored before money moves, explained, queued for a
human, investigated, and fed back as a label**. That pipeline (idempotency, decision record, case, outbox
event, agent, feedback) is identical for every rail. Only the risk assessment differs. So the
architecture is one `ScoringService` with one `RailScorer` per rail:

```
POST /v1/payments/score  {"rail": "card" | "bank_transfer" | "mobile_money" | "crypto", ...}
        |
        v
ScoringService: idempotency -> scorer[rail].assess() -> ONE DB transaction (payment, decision, case, outbox)
        |                              |
        |        card           LightGBM + expected-loss policy + rules            (IEEE-CIS, real)
        |        bank_transfer  APP-scam / mule scorecard + rules
        |        mobile_money   SIM-swap / cash-out / mule scorecard + rules
        |        crypto         OFAC screening + counterparty intelligence + exchange scorecard + Travel Rule
        v
same case queue -> same investigation agent (rail-aware tool: get_payment_risk_signals) -> labels
```

Each decision also returns `required_actions`, because approve/review/decline does not cover every rail:

| Situation | Decision | Required actions |
|---|---|---|
| Crypto withdrawal to a sanctioned address | decline | `block_withdrawal`, `file_sanctions_report` |
| Crypto **deposit** from a sanctioned address | review (cannot decline: funds are already on-chain) | `freeze_funds`, `file_sanctions_report` |
| Risky crypto deposit | review | `hold_credit` |
| Risky bank transfer / mobile money | review | `hold_payment` (push payments are irrevocable once sent) |

## 2. Research: how the industry does it

**Crypto analytics providers** (Chainalysis KYT, Elliptic, TRM Labs) screen wallets and score transaction
risk in real time. They build their intelligence by clustering addresses into the entities that control
them and attributing those entities (exchanges, mixers, darknet markets, sanctioned parties). Risk then
propagates as *direct* exposure (the counterparty itself) and *indirect* exposure (one or more hops away),
weighted by hop distance, value share, attribution confidence and timing. It is never a binary flag.
TRM's "glass box" approach exposes the evidence and confidence behind each label, which is the same
principle as PayGuard's reason codes and grounded agent reports.

**Multi-rail fraud platforms.** Sardine scores ACH, wire, RTP/FedNow, push-to-card, card and stablecoins
through one API, with 50-150 ms real-time decisions. Unit21 focuses on post-transaction monitoring, case
management and SAR workflows. Feedzai's "RiskOps" unifies ingestion, scoring and alert management. Banks
often run real-time decisioning and compliance case management in sequence. PayGuard does both in one
pipeline, with the scoring path kept synchronous and the investigation asynchronous.

**Regulation shaping the crypto rail:**
- **FATF Recommendation 16, the "Travel Rule".** VASPs must exchange originator and beneficiary
  information off-chain. Thresholds: USD/EUR 1,000 FATF baseline, USD 3,000 under the US BSA, and zero
  in the EU since the 2023 Transfer of Funds Regulation. The threshold is a setting
  (`PAYGUARD_TRAVEL_RULE_THRESHOLD_USD`).
- **OFAC SDN list.** Includes digital-currency addresses across BTC, ETH, TRX, USDT, USDC, XMR and other
  chains. A hit is a hard stop, not a score.
- **MiCA (EU)** and proposed US legislation are making blockchain-analytics tooling a baseline
  expectation for regulated exchanges.

**Typologies and the signals that encode them:**

| Rail | Typology | Signal in PayGuard |
|---|---|---|
| Bank transfer | APP scam: victim coached into paying a mule | first payment to this payee, large amount, odd hour, amount far above the sender's usual |
| Bank transfer | Mule account receiving from many victims | beneficiary fan-in: distinct senders in 72h |
| Mobile money | SIM-swap account takeover | transfer or cash-out within 48h of a SIM swap |
| Mobile money | Rapid cash-out network | distinct wallets cashing out at one agent in 24h |
| Crypto | Account takeover, then withdraw to attacker | new account, new withdrawal address, large amount |
| Crypto | Mule / pig-butchering pass-through | most of the last 24h of deposits withdrawn within 30 min |
| Crypto | Collection address for a fraud ring | one external address used by many of our accounts |
| Crypto | Sanctioned counterparty | OFAC SDN address screening |
| Crypto | Illicit funds arriving | counterparty intelligence (below) |
| Crypto | Missing originator/beneficiary data | Travel Rule completeness check |

## 3. The crypto intelligence layer

### Data
- **Elliptic++** (KDD 2023): 203,769 real Bitcoin transactions in 49 time steps of about two weeks each,
  labelled illicit (4,545) or licit (42,019) by Elliptic, plus the transaction-address graph. It
  extends the Elliptic dataset of Weber et al. (2019).
- **OFAC SDN digital-currency addresses**: the real list, multi-chain, fetched at ingest with a timestamp.

### Two data traps found and avoided
1. **Elliptic++ wallet features leak the future.** All 55 wallet-level features are lifetime aggregates,
   identical at every time step. At its first time step, a wallet "knows" its last-seen block. A model
   trained on them would look excellent offline and be useless in production. They are not used.
2. **Float32 transaction IDs.** Reading every column as float32 to save memory rounded Elliptic's IDs,
   which reach about 2.3e8 while float32 is exact only to 2^24. Three-quarters of the label join silently
   failed. The ingest now reads IDs as int64 and validates the join one-to-one.

### Transaction risk model
LightGBM, compared against a Random Forest and against graph features. The evaluation is
**strict-inductive and out-of-time**: train on steps 1-34, validate on 35-39, test on 40-49.
Neighbour-score features are computed only from same-time-step neighbours, using out-of-fold
first-stage scores, so no node sees its own training label through the graph. This follows a 2026
re-evaluation (arXiv:2604.19514) showing that much of the reported GNN advantage on Elliptic came from
test-period adjacency leaking into training. Under a strict protocol, a Random Forest beat GraphSAGE.

**Finding:** before the dark-market shutdown at step 43, illicit F1 is about 0.87. After it, **every
model**, with or without graph features, drops to about 0.03. No supervised model trained on past
labels detects the behaviour that replaced the market. That is the argument for the parts of the
system that do not depend on the model.

### Point-in-time address and entity intelligence
A query "as of time step q" sees only transactions before q, and sees an illicit label only once it
has *arrived* (one step later; attribution lags activity in reality). The store holds:
- the model's out-of-sample risk on every indexed transaction;
- per-address activity and known-illicit labels;
- one-hop counterparty exposure;
- **entities from common-input-ownership clustering** (Meiklejohn et al., 2013). Addresses spent
  together as inputs are one owner. Transactions with more than 50 inputs are not unioned, a CoinJoin
  mitigation. Offline evaluation replays the clustering step by step. Serving uses the live clustering,
  which in production *is* "now".

**Finding:** Bitcoin addresses are mostly single-use, so address history covers few counterparties.
Resolving the **sending entity through the scored transaction's co-inputs** raises coverage (see
EVALUATION.md). Clustering only the address in isolation did nothing, because an unseen address has no
cluster. Using the co-inputs is the step that makes it work, and it is how live KYT systems use the
heuristic.

### Counterparty risk combiner
A logistic combiner over transaction risk, address history, counterparty exposure and entity
attribution. It is trained on the validation period, evaluated on the test period, isotonic-calibrated,
and thresholded on validation precision targets: review at 50% precision, block at 95%. Blocking acts on
a customer without a human, so it needs much higher precision than queueing a review. The two thresholds
are kept ordered by an invariant; an earlier version had them inverted, which would have declined
everything above the lower bar.

## 4. Rails without public labelled data

There is no public labelled dataset of APP scams or mobile-money takeovers with these fields (PaySim is
simulated and lacks SIM-swap and payee data). Those rails therefore start as **transparent scorecards**:
log-odds points per condition, in YAML, each with a human-readable reason a compliance reviewer can
audit. This is how teams operate before they have labels. Every analyst resolution and chargeback lands in
the same `labels` table, which is the training set for the model that replaces the scorecard. The
points are priors, and the docs say so.

## 5. Limitations

- Elliptic data is Bitcoin in 2016-17. Account-based chains (Ethereum, Tron) need different clustering
  (no common-input heuristic; use deposit-address reuse and contract interactions instead). On those
  chains the crypto rail currently runs sanctions screening and the behavioural scorecard, without
  on-chain intelligence, and says so in its model version.
- Exchange behaviour features (pass-through, collection addresses) are tested on constructed scenarios.
  There is no public labelled exchange-account dataset.
- Exposure here is one hop and count-based. Production systems trace value-weighted taint over multiple
  hops (haircut or FIFO allocation) and across chain bridges.
- Elliptic time steps are mapped to calendar dates approximately (about 2 weeks each, anchored at
  block 439,586). Only relative order matters for correctness.

## Sources

- [Crypto AML compliance for exchanges, 2026 regulatory map (AML Watcher)](https://amlwatcher.com/blog/aml-compliance-crypto-exchanges-2026-regulatory-map/)
- [Know Your Transaction: crypto transaction monitoring (AMLBot)](https://amlbot.com/transaction-monitoring)
- [Chainalysis vs Elliptic vs TRM Labs, 2026 comparison (FinConduit)](https://finconduit.com/resources/blockchain-analytics-providers-compared)
- [Crypto AML tool comparison (Spark)](https://www.spark.money/tools/crypto-aml-tool-comparison)
- [Direct vs indirect exposure in crypto AML (BlockSec)](https://blocksec.com/aml/direct-vs-indirect-exposure-crypto-aml)
- [Crypto mixers and sanctions compliance (Elliptic)](https://www.elliptic.co/insights/crypto-mixers-and-privacy-protocols-the-sanctions-compliance-implications/)
- [FATF Travel Rule requirements, 2026 guide (InvestGlass)](https://www.investglass.com/fatf-travel-rule-requirements-a-2026-guide-for-financial-institutions-vasps-and-crypto-businesses/)
- [Crypto Travel Rule explained (Sumsub)](https://sumsub.com/blog/what-is-the-fatf-travel-rule/)
- [OFAC sanctioned digital-currency address extraction (0xB10C)](https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses)
- [Elliptic Bitcoin dataset overview (Kumo)](https://kumo.ai/pyg/datasets/elliptic-bitcoin/)
- [Elliptic++ dataset (Hugging Face mirror)](https://huggingface.co/datasets/AI4FinTech/ellipticpp)
- [When graph structure becomes a liability: GNNs for Bitcoin fraud under temporal shift (arXiv:2604.19514)](https://arxiv.org/abs/2604.19514)
- [The shape of money laundering: Elliptic2 subgraph learning](https://www.semanticscholar.org/paper/The-Shape-of-Money-Laundering:-Subgraph-Learning-on-Bellei-Xu/8caf4735a73a7b26f724cac889cb814f868e12ef)
- [Sardine multi-rail coverage (Sardine vs Unit21, FluxForce)](https://www.fluxforce.ai/blog/sardine-vs-unit21)
- [Sardine payment fraud](https://www.sardine.ai/payment-fraud)
- [Money mule detection: APP fraud and AML convergence (AML Watcher)](https://amlwatcher.com/blog/money-mule-detection-app-fraud-aml/)
- [What is APP fraud (Feedzai)](https://www.feedzai.com/blog/what-is-app-fraud/)
- [Crypto fraud on centralized exchanges (Fingerprint)](https://fingerprint.com/blog/fighting-fraud-crypto-VASP/)
- [14 crypto scam types (TRM Labs)](https://www.trmlabs.com/resources/blog/14-crypto-scam-types-and-how-blockchain-forensics-helps-detect-and-disrupt-them)
