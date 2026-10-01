# Runbook

What to do when an alert fires. Every metric named here is exported at `GET /metrics`.

## Score latency p99 > 50 ms

1. Check `payguard_score_stage_seconds` by `stage`:
   - `features` is slow: online store latency or contention. Redis `WATCH` conflicts retry, and a hot entity
     (for example a very popular device fingerprint) serialises its writers. Check Redis latency and CPU.
   - `model` is slow: a challenger was added (shadow scoring doubles model time) or the model grew. Check
     `GET /v1/models`.
   - `persist` is slow: the database. Check connection pool saturation and lock waits.
2. Mitigation: unset the challenger (`POST /v1/models/promote {"alias": "challenger", "version": null}`),
   which takes effect immediately with no restart.

## Degraded scoring (`payguard_degraded_scores_total` increasing, `/readyz` returns 503)

The online feature store is unreachable. Payments keep flowing: transactions are scored on request data
and processor signals only, with the reason code `degraded`. Protection is weaker. The ablation
`no_streaming` shows roughly the size of the loss (see `docs/EVALUATION.md`).

1. Restore Redis. State has a 180-day TTL and Redis runs with AOF, so a restart recovers it.
2. If state was lost, rebuild it with `payguard backfill && payguard materialize`, run from the event log.
3. Transactions scored while degraded did not update velocity state. That is an accepted gap. The
   backfill in step 2 closes it.

## Drift alert (`GET /v1/monitoring/drift` returns `status: alert`)

Labels arrive weeks late, so this is the earliest warning of model decay.

1. Look at `drifted_features`. If it is **one processor signal** (for example `sig_C13` collapsing), it
   is almost always an **upstream data bug**, not a change in fraud. Contact the upstream processor.
   Do not retrain on broken data.
2. If `score_psi` is high while features look stable, the traffic mix changed (a new merchant, product
   or region). Compare `flag_rate` with review capacity.
3. If `live_precision_flagged` (from analyst labels) drops, the model is decaying. Train a challenger,
   run it in shadow and compare it on labelled traffic, then promote. Roll back with a promote to the
   previous version.

`payguard drift-demo` reproduces a realistic incident: C13 zeroed, and 30% of amounts sent in minor units.
Use it to rehearse.

## Review rate above analyst capacity

The policy was fitted for 3% review capacity on the validation month.

1. Check whether a rule was switched to `enforce` (`payguard_rule_hits_total{mode="enforce"}`).
2. Check drift (above). A shifted score distribution moves volume across the expected-loss threshold.
3. Short term, raise the effective review threshold by retraining the policy with the actual capacity
   (`configs/policy.yaml`).

## Outbox not draining (`payguard_outbox_unpublished` growing)

The relay cannot publish. Cases are still created, because they are written in the scoring transaction,
but investigations will not start. Check the bus (Redis) and the worker logs. Events are not lost: they
publish in order when the relay recovers, and consumers are idempotent, so duplicates are harmless.

## Investigations failing (`payguard_agent_runs_total{status="failed"}`)

The agent is advisory, so analysts keep working without it. `error` on the investigation says why: rate
limited, API error, refusal, or no report within the step limit. Set `PAYGUARD_AGENT_PROVIDER=heuristic`
to fall back to the deterministic investigator while the LLM provider recovers.

## Model rollback

```bash
payguard promote champion <previous-version> --reason "rollback: <incident link>"
# or, with no restart:  POST /v1/models/promote {"alias": "champion", "version": "<previous>", "reason": "..."}
```

The registry keeps the full promotion history, including who promoted what, when and why.
