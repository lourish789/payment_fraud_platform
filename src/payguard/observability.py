"""Prometheus metrics. Scraped at GET /metrics."""

from prometheus_client import Counter, Gauge, Histogram

LAT_BUCKETS = (0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.2, 0.5, 1.0, 2.5)

HTTP_LATENCY = Histogram("payguard_http_request_seconds", "HTTP latency", ["route", "method", "status"], buckets=LAT_BUCKETS)
SCORE_STAGE = Histogram("payguard_score_stage_seconds", "Scoring latency by stage", ["stage"], buckets=LAT_BUCKETS)
DECISIONS = Counter("payguard_decisions_total", "Decisions", ["decision", "model_version"])
RULE_HITS = Counter("payguard_rule_hits_total", "Rule hits", ["rule", "mode"])
FRAUD_PROB = Histogram("payguard_fraud_probability", "Calibrated fraud probability",
                       buckets=(0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 0.8, 0.95))
DEGRADED = Counter("payguard_degraded_scores_total", "Decisions made without the online feature store")
IDEMPOTENT_REPLAYS = Counter("payguard_idempotent_replays_total", "Requests answered from the idempotency record")
RATE_LIMITED = Counter("payguard_rate_limited_total", "Requests rejected by the rate limiter", ["client"])
OUTBOX_LAG = Gauge("payguard_outbox_unpublished", "Outbox rows not yet published")
AGENT_RUNS = Counter("payguard_agent_runs_total", "Investigations", ["provider", "status"])
AGENT_TOKENS = Counter("payguard_agent_tokens_total", "LLM tokens", ["direction"])
RECEIPTS = Counter("payguard_receipts_total", "Receipt verifications", ["verdict"])
