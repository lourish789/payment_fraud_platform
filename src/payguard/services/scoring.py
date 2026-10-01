"""Synchronous scoring path. Budget: p99 < 50 ms on one core, excluding network.

    1. idempotency   same transaction_id + same payload -> stored decision; different payload -> 409
    2. features      atomic read-modify-write on the online store (deduped by transaction_id)
    3. model         champion -> calibrated p; challenger scored in shadow (logged, never acted on)
    4. decision      expected-loss policy, then rules; the most severe wins
    5. persist       transaction + decision + case + outbox events in ONE DB transaction

Model explanations (TreeSHAP, 60-100 ms vs ~1 ms to predict) are deliberately NOT computed here; see
services/explanations.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from payguard import observability as obs
from payguard.db.models import Case, DecisionRecord, Transaction, clean_json
from payguard.db.session import write_guard
from payguard.events import enqueue
from payguard.features.pipeline import FeaturePipeline, entity_keys
from payguard.models.registry import ModelBundle, Registry
from payguard.rules import RuleEngine, most_severe
from payguard.schemas import Decision, ReasonCode, ScoreResponse, TransactionIn


log = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    """Same transaction_id re-submitted with a different payload."""


def payload_hash(txn: TransactionIn) -> str:
    body = json.dumps(txn.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class ModelHolder:
    """Champion/challenger bundles, swappable at runtime (promotion reloads without a restart)."""

    def __init__(self, registry: Registry):
        self.registry = registry
        self._lock = threading.Lock()
        self.champion: ModelBundle | None = None
        self.challenger: ModelBundle | None = None
        self.reload()

    def reload(self) -> None:
        reg = self.registry.read()
        champ = self.registry.load("champion") if reg.get("champion") else None
        chall = self.registry.load("challenger") if reg.get("challenger") else None
        with self._lock:
            self.champion, self.challenger = champ, chall

    def current(self) -> tuple[ModelBundle | None, ModelBundle | None]:
        with self._lock:
            return self.champion, self.challenger


@dataclass
class ScoringService:
    pipeline: FeaturePipeline
    models: ModelHolder
    rules: RuleEngine
    session_factory: sessionmaker

    def score(self, txn: TransactionIn, client_id: str) -> ScoreResponse:
        t0 = time.perf_counter()
        phash = payload_hash(txn)
        existing = self._existing(txn.transaction_id, phash)
        if existing is not None:
            return existing

        champion, challenger = self.models.current()
        if champion is None:
            raise RuntimeError("no champion model registered")

        t = time.perf_counter()
        degraded = False
        try:
            feats, _ = self.pipeline.build(txn)
        except Exception:
            # Fail soft: an online-store outage must not stop payments. Score on request + processor
            # signals only, flag the decision, alert on the metric. (Retries of a degraded request are
            # still idempotent via the DB record.)
            log.exception("online feature store unavailable; scoring degraded")
            obs.DEGRADED.inc()
            feats, degraded = self.pipeline.build_degraded(txn), True
        obs.SCORE_STAGE.labels("features").observe(time.perf_counter() - t)

        t = time.perf_counter()
        _, raw, p = champion.predict_row(feats)
        model_decision = champion.policy.decide(p, txn.amount)
        rule_decision, hits = self.rules.evaluate(feats)
        decision = most_severe(model_decision, rule_decision)
        # Client-facing reason codes are coarse on purpose; per-feature explanations are computed off the
        # request path for analysts (services/explanations.py).
        reasons = []
        if model_decision != Decision.APPROVE:
            reasons.append(ReasonCode(code="model_risk", detail="Elevated fraud risk", weight=round(p, 4)))
        reasons += [ReasonCode(code=f"rule:{r.id}", detail=r.description, weight=0.0) for r in hits if r.mode == "enforce"]
        if degraded:
            reasons.append(ReasonCode(code="degraded", detail="Behavioural history unavailable; scored on request data only"))
        shadow = None
        if challenger is not None:
            _, s_raw, s_p = challenger.predict_row(feats)
            shadow = {"version": challenger.version, "fraud_probability": s_p,
                      "decision": challenger.policy.decide(s_p, txn.amount).value}
        obs.SCORE_STAGE.labels("model").observe(time.perf_counter() - t)

        latency_ms = (time.perf_counter() - t0) * 1000
        keys = entity_keys(txn)
        t = time.perf_counter()
        case_id = None
        try:
            with write_guard(self.session_factory), self.session_factory() as s, s.begin():
                s.add(Transaction(id=txn.transaction_id, client_id=client_id, payload=txn.model_dump(mode="json"),
                                  payload_hash=phash, event_time=txn.event_time, amount=txn.amount,
                                  bin_key=keys["bin"], customer_key=keys["customer"], device_key=keys["device"]))
                s.flush()  # surface a concurrent duplicate as IntegrityError before the other inserts
                s.add(DecisionRecord(transaction_id=txn.transaction_id, decision=decision.value, fraud_probability=p,
                                     raw_score=raw, expected_loss=p * txn.amount, model_version=champion.version,
                                     reasons=[r.model_dump() for r in reasons],
                                     rules=[r.id if r.mode == "enforce" else f"shadow:{r.id}" for r in hits],
                                     features=clean_json(feats), shadow=shadow, latency_ms=latency_ms))
                event = {"transaction_id": txn.transaction_id, "decision": decision.value, "fraud_probability": p,
                         "amount": txn.amount, "model_version": champion.version, "shadow": shadow,
                         "degraded": degraded}
                enqueue(s, "decision.made", txn.transaction_id, event)
                if decision != Decision.APPROVE:
                    case = Case(transaction_id=txn.transaction_id, priority=p * txn.amount, decision=decision.value)
                    s.add(case)
                    s.flush()
                    case_id = case.id
                    enqueue(s, "case.created", case.id, {"case_id": case.id, **event})
        except IntegrityError:
            # Lost a race with a concurrent request carrying the same transaction_id.
            existing = self._existing(txn.transaction_id, phash)
            if existing is None:
                raise
            return existing
        obs.SCORE_STAGE.labels("persist").observe(time.perf_counter() - t)

        obs.DECISIONS.labels(decision.value, champion.version).inc()
        obs.FRAUD_PROB.observe(p)
        for r in hits:
            obs.RULE_HITS.labels(r.id, r.mode).inc()
        return ScoreResponse(transaction_id=txn.transaction_id, decision=decision, fraud_probability=round(p, 6),
                             expected_loss=round(p * txn.amount, 4), reasons=reasons, model_version=champion.version,
                             rules_triggered=[r.id for r in hits if r.mode == "enforce"], case_id=case_id,
                             latency_ms=round((time.perf_counter() - t0) * 1000, 3), degraded=degraded)

    def _existing(self, transaction_id: str, phash: str) -> ScoreResponse | None:
        with self.session_factory() as s:
            row = s.execute(select(Transaction, DecisionRecord)
                            .join(DecisionRecord, DecisionRecord.transaction_id == Transaction.id)
                            .where(Transaction.id == transaction_id)).first()
            if row is None:
                return None
            tx, dec = row
            if tx.payload_hash != phash:
                raise IdempotencyConflict(transaction_id)
            case_id = s.scalar(select(Case.id).where(Case.transaction_id == transaction_id))
        obs.IDEMPOTENT_REPLAYS.inc()
        return ScoreResponse(transaction_id=transaction_id, decision=Decision(dec.decision),
                             fraud_probability=round(dec.fraud_probability, 6),
                             expected_loss=round(dec.expected_loss, 4),
                             reasons=[ReasonCode(**r) for r in dec.reasons], model_version=dec.model_version,
                             rules_triggered=[r for r in dec.rules if not r.startswith("shadow:")],
                             case_id=case_id, latency_ms=dec.latency_ms,
                             idempotent_replay=True)
