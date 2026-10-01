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

## What I would do next

- Replace the dev-mode SQLite with Postgres everywhere (compose already does) and add Alembic migrations.
- Partitioned backfill, and a feature registry with owners and freshness SLOs.
- Label-delay-aware retraining: train only on transactions whose chargeback window has closed.
- pgvector for `find_similar_cases` instead of the in-process scan.
- A small labelled set of real receipts (with consent) to replace the synthetic vision evaluation.
