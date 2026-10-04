# Design decisions

Short architecture decision records: the choice, the alternatives, and what would change the decision.

## ADR-1: One feature code path for training and serving

**Decision.** Streaming features are computed by one function (`FeaturePipeline.build`). The offline
backfill replays the event log, in event-time order, through that same function. The online API calls it
per request.

**Why.** Training/serving skew is the most common silent failure of production fraud models. Having a
separate SQL or Spark implementation for training and a Python one for serving guarantees drift between
them. Using a single code path makes skew testable: `payguard parity` compares the features stored with
every served decision against the training rows for the same transactions.

**Cost.** Backfill is single-threaded Python (about 1,600 transactions/s, 6 minutes for 590k). At 100x the
data I would partition the replay by entity key: state is per entity, so partitions are independent. I
would still not fork the feature logic.

## ADR-2: Exponentially decayed aggregates instead of exact sliding windows

**Decision.** Velocity and spend features are exponentially decayed counts and sums (half-lives of 1h,
24h, 7d and 30d), stored as O(1) state per entity.

**Why.** Exact 7-day windows need every event in the window per entity (unbounded memory for hot
entities, and costly expiry). Decayed aggregates need five floats. The update is **commutative**, so a
late or out-of-order event produces the same state as in-order delivery (property-tested in
`test_fold_is_order_independent`).

**Trade-off.** "cnt_1h" means "decayed count with a 1-hour half-life", not "count in the last hour". That
is fine for a model, but it has to be explained to analysts, which is why the reason codes say "(decayed)".

## ADR-3: Atomic, idempotent online feature updates

**Decision.** Read-state, compute-features and write-state happen in one optimistic transaction
(Redis `WATCH`/`MULTI`), guarded by a per-transaction dedupe key that stores the computed features.

**Why.** Two failure modes break velocity features in production:
1. **Retries double count.** A client retrying a timed-out request would add the event twice. The
   dedupe key makes the retry return the original features and write nothing.
2. **Concurrent requests for the same card lose updates.** A plain read-modify-write loses one of two
   concurrent updates. `WATCH` detects the conflict and one side retries.

A Lua script would be faster, but the fold logic would then exist twice (Lua online, Python offline),
which reintroduces the skew ADR-1 removes.

## ADR-4: Transactional outbox instead of dual writes

**Decision.** The decision, the case and the events announcing them are written in **one database
transaction**. A relay publishes outbox rows to the bus afterwards.

**Why.** Writing to the DB and then publishing to a queue (or the reverse) loses or invents events when
the process dies between the two. The outbox gives at-least-once delivery, and every consumer is
idempotent: `enqueue_investigation` reuses an unfinished investigation per case, and case creation is
unique per transaction.

## ADR-5: Redis Streams rather than Kafka

**Decision.** The event bus is Redis Streams with consumer groups (`XREADGROUP`, `XACK`, `XAUTOCLAIM` for
crashed consumers), behind an `EventBus` interface.

**Why.** At this scale Redis is already required for the online store. Streams give per-group offsets,
at-least-once delivery and redelivery of un-acked messages. Adding Kafka would double the operational
surface for no capability this system uses.

**When to revisit.** Long retention and replay for multiple downstream teams, or more than about
50k events/s. The interface is the seam for a Kafka adapter.

## ADR-6: Decisions from expected loss under a capacity constraint, not a fixed threshold

**Decision.** Review when `p x amount > c`, where `c` starts at the cost of a review ($5) and is raised
until the review rate fits analyst capacity (3%). Decline only when `p` is at or above a threshold with
at least 90% validation precision.

**Why.** A fixed probability threshold treats a $3 and a $3,000 transaction the same. The expected-loss
rule is the Bayes-optimal decision for this cost model. The capacity constraint is what binds in real
fraud operations, and the fitted `c` ($28.41) is its shadow price: at capacity, the marginal review is
worth $28 of expected fraud, not $5. This only works because the model is **calibrated** (isotonic,
ECE 0.003 on the test month). Uncalibrated scores would make `p x amount` meaningless.

## ADR-7: Rules are measured and demoted to shadow when they don't pay

**Decision.** Rules have `mode: enforce | shadow`. Each rule's *marginal* precision (its hits among
transactions the model approved) is measured on the validation month. The four heuristic rules all came
in below the base fraud rate and run in shadow. Only the hard amount limit, a business policy, is enforced.

**Why.** "Model plus rules" was measurably worse than the model alone (see the evaluation). Hand-written
rules are usually added after an incident and never removed. Measuring them is the only defensible way
to decide which ones stay.

## ADR-8: The agent recommends, it never acts

**Decision.** The investigation agent has read-only tools and its only output is a structured report.
The analyst makes the decision.

**Why.**
- **Prompt injection.** Customer-controlled strings (device names, email domains, receipt text) flow
  into the model's context. Because the agent cannot act, a successful injection costs analyst time,
  not money.
- **Grounding.** Every evidence item must cite a tool call, and numbers are checked against it
  mechanically (`check_grounding`).
- **Point-in-time tools.** The tools cannot see the case's own label, or any label that had not yet
  arrived when the case was raised. Otherwise the offline evaluation would be optimistic.

**Build vs. buy.** The loop is a manual Messages API loop rather than the SDK tool runner, because I need
a hard step limit, token accounting per case, a full trace, and report validation before accepting an
answer. A deterministic heuristic provider shares the same tools. It is the fallback when the LLM is
unavailable and **the baseline the LLM has to beat**.

## ADR-9: Receipts are reconciled against the ledger first; forensics are secondary

**Decision.** OCR extracts amount, reference and date, and the ledger is the source of truth. Error Level
Analysis runs as well, and is the only signal when the ledger cannot be consulted.

**Why.** Pixel forensics are an arms race. Ledger reconciliation is not: a fake receipt for a transfer
that does not exist fails regardless of image quality. The forensic band is calibrated on genuine
receipts to a false-flag target, and evaluated separately in a "ledger unavailable" regime so its
limits are visible.

## ADR-10: Explanations are computed off the request path

**Decision.** The scoring response carries coarse reason codes (`model_risk`, enforced rule IDs).
Per-feature TreeSHAP explanations are computed asynchronously when a case is created, and on first read
otherwise, then cached on the decision record.

**Why.** Profiling showed TreeSHAP costs **60 to 100 ms per transaction** (measured at 64, 77 and 96 ms
across three model versions of roughly 1,000 to 1,500 trees), against about 1 ms for the prediction itself.
Run inline for flagged transactions, it alone would break a 50 ms p99. Only analysts and the agent read
explanations, and only for flagged transactions. The explanation is still exact rather than approximate:
the stored serving-time feature snapshot is re-encoded by the same model version that made the decision.

**Side benefit.** Merchants no longer receive model internals. Detailed reason codes on the client side
help fraudsters probe which features to spoof.

## ADR-11: Features must be stationary; the drift monitor found three that were not

**What happened.** The first drift drill raised an `alert` on *healthy* traffic:

- `bin_age_days` had PSI 4.4.
- `device_browser` had PSI 0.90.

**Root causes.**
1. **Left censoring.** The data starts on 2017-12-01, so "days since first seen", "transactions since
   first seen" and "distinct devices ever seen" measure time since the dataset began, not the entity's
   true history. They grow every month by construction, so May always looks different from April. Early
   December rows are also mislabelled "new".
2. **Version churn and an upstream format change.** Raw browser strings ("chrome 65.0" → "chrome 66.0")
   change with every release. After normalising versions away, the monitor *still* flagged the browser
   feature (PSI 0.55). The source had changed format between months: April reports `mobile safari generic`
   and `chrome generic for android`, May reports `mobile safari 11.0`. Same browsers, different strings.
   That is exactly the class of upstream change the monitor exists to catch.

**Decision.**
- Age is capped at 30 days.
- The long-run count is a 30-day decayed count.
- Distinct counts use a 30-day window (last-seen timestamps, keeping the 32 most recent, still commutative).
- Browser and OS are normalised to family (plus the OS major version). Browser normalisation also strips
  the `generic` and `for <platform>` suffixes.
- December only warms up entity state and is excluded from training rows.

**Cost, measured on the May test month:**

| | Before (non-stationary) | After (stationary) |
|---|---|---|
| ROC-AUC | 0.913 | 0.909 |
| PR-AUC | 0.545 | 0.527 |
| Net savings | $310k | $303k |

Part of that "lost" accuracy came from features that could only get worse: their train/serve gap widens
every month the model stays in production. I accepted it.

**Lesson.** A drift monitor that alerts on healthy traffic gets muted, and then it misses the real
incident. Running the drill on clean data first is what exposed this.

## ADR-12: One scoring pipeline, one scorer per payment rail

**Decision.** `ScoringService` owns idempotency, persistence, cases and the outbox. A `RailScorer` per
rail owns features, models, rules and intelligence, and returns an `Assessment`. Scorers never touch
the database; the service never knows how a rail scores.

**Why.** "Every payment method is reviewable" is a property of the pipeline, not of each model. With one
pipeline, a new rail inherits retries, audit trail, analyst queue, agent, labels and metrics for free.
The alternatives were a separate service per rail, which means duplicated case management and split
queues, or one giant model over all rails, which means incompatible features and no per-rail
accountability. Both are worse.

**Detail.** `required_actions` extends approve/review/decline because rails differ in what is
reversible. A crypto deposit is already on-chain and cannot be refused, so a "decline" becomes
`freeze_funds`. Push payments are irrevocable once sent, so a review means `hold_payment` first.

## ADR-13: Sanctions screening is a control, not a feature

**Decision.** An OFAC SDN address hit decides the outcome directly (block or freeze, plus a report)
before any model or scorecard is consulted. Screening is conservative: EVM addresses are matched
case-insensitively, bech32 addresses are lower-cased, base58 addresses are matched case-sensitively, and
a string listed under any asset matches regardless of the chain the caller claims.

**Why.** A sanctions match is a legal obligation. Blending it into a probability would let other signals
dilute it, and a model can't be audited the way a list match can.

## ADR-14: Crypto intelligence is evaluated strictly time-ordered, and the leaky features are refused

**Decision.**
- Train, validate and test on disjoint, ordered time steps.
- Compute graph features only from same-step neighbours, with out-of-fold first-stage scores.
- Make illicit labels visible only after a delay.
- Replay entity clustering step by step.
- Don't use the Elliptic++ wallet features (lifetime aggregates).
- Read transaction IDs as int64 and validate the label join.

**Why.** Every one of these traps was real here. Wallet features would have leaked the future. Float32
IDs silently dropped three-quarters of the labels. Published GNN results on this dataset were inflated
by test-period adjacency. The honest result (F1 0.87 before the dark-market shutdown, 0.03 after) is
more useful than a leaky 0.95, because it says what the model cannot do.

## ADR-15: Review and block thresholds come from precision targets

**Decision.** Counterparty-risk thresholds are the lowest calibrated scores reaching 50% (review) and
95% (block) precision on validation, with `review <= block` enforced.

**Why.** An earlier version defined "block" as the lowest score reaching 90% precision and "review" as
the max-F1 point. These came out inverted (block 0.27 < review 0.67), which would have declined
everything above the lower bar and left the review band empty. Tying each band to the cost of its
error (an analyst's time vs. a customer blocked without a human) makes the ordering structural rather
than accidental.

## ADR-16: The API serves the console from its own origin

**Decision.** The React console is built to static files and served by the FastAPI process
(`/assets/*` plus an `index.html` fallback for client-side routes). Docker builds both in one multi-stage
image, and Node never reaches the runtime image.

**Why.**
- **One deployable.** There is no CDN/API version skew and no separate hosting to secure.
- **No CORS.** The browser sees one origin, so the CSP can say `connect-src 'self'`. That is the control
  that makes a bearer key in `sessionStorage` acceptable for an internal console: no third-party script
  can run, and nothing can be sent off-origin.

**When to revisit.** At high console traffic, or when a CDN's edge caching matters. The build is plain
static files, so moving it to a CDN is a deployment change only. CORS is already a setting.

## ADR-17: Read models and one error contract for the console

**Decision.**
- Routers are thin, one per resource. Queries live in `services/queries.py` and dashboard aggregates in
  `services/admin.py`.
- Every route declares a response model (`api/dto.py`), mirrored by `frontend/src/api/types.ts`.
- Lists are `{items, total, limit, offset}`.
- Every error is `{"error": {"code", "message", "request_id"}}`.

**Why.** A UI is the API's most demanding client. It needs filters, pages, totals and errors it can show
to a person and trace to a log line. Before this change the API returned bare lists, two error shapes
(`detail` from FastAPI and `error` from the middleware), and untyped dicts.

**Cost.** This was a breaking change to `GET /v1/cases`, which went from a list to a page, and to error
bodies. It was acceptable pre-release; after release it would need a `/v2` or a deprecation window.

## ADR-18: Admin aggregates in SQL, over covering indexes

**Decision.**
- The overview is a handful of GROUP BY queries, one of which covers traffic, decisions, rails, labels and
  the confusion matrix in a single scan.
- Covering indexes make those scans index-only.
- Percentiles and rule hits use the most recent 20k rows.

**Why.** Computing in Python over all rows works at 10k and fails at 10M. Separate count queries over wide
JSON rows took 7.4 s on 89k transactions; the grouped, index-only version takes 0.6 s.

**When to revisit.** At tens of millions of rows, maintain hourly rollups. Either update them
asynchronously from the `decision.made` stream, or use a materialised view in Postgres. Don't update them
on the scoring path, where one row per hour and rail would become a lock hotspot.

## ADR-19: Risk is computed in USD; payments keep their own currency

**Context.** The QA pass found that `currency` was free text and ignored. ₦46,500 on a card (about $30) was
scored as $46,500: the hard amount rule declined it, with an expected loss of $5,066. A crypto payment without
`amount_usd` scored 0.5 BTC as $0.50, which slips under the Travel Rule and the large-transfer points. The
dashboard summed naira and dollars into one "volume".

**Decision.**
- `configs/currency.yaml` holds reference rates (units per USD; USD and NGN shipped). The scoring service
  converts every payment to USD before any scorer sees it. The caller's own `amount_usd` always wins.
- An unknown currency is rejected (`422 unsupported_currency`), never guessed. Crypto without `amount_usd`
  is rejected (`amount_usd_required`), because asset prices move by the minute.
- `transactions.amount` is now USD, so aggregates and filters are currency-safe. `currency` and
  `amount_local` keep the payment as submitted. The stored payload, and therefore its idempotency hash, is
  exactly what the caller sent. A one-time migration re-bases older rows.
- Score responses add `currency`, `amount`, `amount_usd`, `fx_rate` and `expected_loss_local`.
  `expected_loss` stays USD, so existing integrations keep their meaning.
- Receipts are reconciled against the local amount and currency. A receipt showing the right digits in the
  wrong currency is a `mismatch`.

**Why not convert in each scorer.** One boundary means one place to audit the rate that was applied
(`fx_rate` is in the response and derivable from the row), and the model and scorecards stay currency-blind.

**When to revisit.** The rate is static. A production deployment would refresh it daily from a treasury
source (for NGN, the CBN closing rate), and record the rate's timestamp on each decision.

## ADR-20: Languages are rendered on read; codes never change

**Context.** Users may be English, Yorùbá, French, Hausa, Igbo or Pidgin speakers. Two kinds of text reach
them: the console's own labels, and text the API writes (errors, decision reasons, rule and scorecard texts,
model explanations, the deterministic agent's report).

**Decision.**
- **gettext style, English as the message id**, in both tiers. `t("Case queue")` in the console;
  `payguard/locales/<lang>.json` in the API. Untranslated text falls back to English instead of breaking.
- **The API translates on the way out.** Decisions, explanations and reports are stored in English, as
  audit records, and translated when read. A template matcher handles generated text such as
  "Customer activity in the last hour: 3.0 prior transactions", including rows stored before a translation
  existed. Error `code`s, reason `code`s, rule ids and `required_actions` never change; integrations branch
  on those.
- **Which language:** `?lang=`, then the API key's profile (`PATCH /v1/auth/me/preferences`), then
  `Accept-Language`, then `PAYGUARD_DEFAULT_LOCALE`. Responses carry `Content-Language`. The profile also
  holds the display currency (USD or NGN), so both follow the user to any browser.
- **Completeness is tested.** A Vitest test scans the console for every `t()`/`msg()` literal (435
  strings) and fails if any language misses one or changes a `{placeholder}`. A pytest test does the same
  for the API catalogs.

**Limits.** The Yorùbá, Hausa, Igbo and Pidgin translations were drafted without a native-speaker review.
They need one before real users see them. Free-text reports written by the LLM agent stay in the language
the model wrote them in.

## What I would do next

- Replace the dev-mode SQLite with Postgres everywhere (compose already does) and add Alembic migrations.
- Partitioned backfill, and a feature registry with owners and freshness SLOs.
- Label-delay-aware retraining: train only on transactions whose chargeback window has closed.
- pgvector for `find_similar_cases` instead of the in-process scan.
- A small labelled set of real receipts (with consent) to replace the synthetic vision evaluation.
- Crypto: multi-hop, value-weighted taint tracing; entity resolution for account-based chains (deposit
  address reuse, contract interactions); cross-chain bridge tracing.
- Console: OIDC SSO with short-lived HttpOnly sessions instead of API keys in browser storage; per-user
  identities in the audit log.
- Replace the bank-transfer and mobile-money scorecards with trained models once resolutions and
  scheme reimbursement claims provide labels.
