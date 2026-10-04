"""Synchronous scoring path, shared by every payment rail. Budget: p99 < 50 ms on one core, excluding network.

    1. idempotency   same transaction_id + same payload -> stored decision; different payload -> 409
    2. currency      convert to USD, the currency every model, scorecard and threshold is in (payguard/currency.py)
    3. assess        the rail's scorer (services/rails.py): features, model/scorecard, rules, intelligence
    4. persist       transaction + decision + case + outbox events in ONE DB transaction

Rail scorers never touch the database; this service never knows how a rail scores. That boundary is
what makes "every payment method reviewable": all rails produce the same decision record, the same
case in the same queue, and the same events for the agent and monitoring.

Model explanations for cards (TreeSHAP, 60-100 ms vs ~1 ms to predict) are computed off the request
path (services/explanations.py); scorecard rails store their point contributions directly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from payguard import observability as obs
from payguard.currency import Converted, FxTable
from payguard.db.models import Case, DecisionRecord, Transaction, clean_json
from payguard.db.session import write_guard
from payguard.events import enqueue
from payguard.models.registry import ModelBundle, Registry
from payguard.schemas import Decision, ReasonCode, ScoreResponse

log = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    """Same transaction_id re-submitted with a different payload."""


class RailNotEnabled(Exception):
    pass


def payload_hash(payment) -> str:
    # `rail` is excluded so card requests hash identically whether they arrive on /v1/transactions/score
    # or /v1/payments/score.
    body = json.dumps(payment.model_dump(mode="json", exclude={"rail"}), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def stored_money(tx: Transaction) -> Converted:
    """The conversion a stored transaction was scored with (rows from before currencies were tracked are USD)."""
    currency = tx.currency or (tx.payload or {}).get("currency") or "USD"
    local = tx.amount_local if tx.amount_local is not None else tx.amount
    given = (tx.payload or {}).get("amount_usd")
    rate = None if given is not None else (local / tx.amount if tx.amount else None)
    return Converted(currency, local, tx.amount, rate)


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


def _money(m: Converted, expected_loss_usd: float) -> dict:
    # Expected loss in the payment's own currency, at the rate the payment was converted with.
    rate = m.fx_rate if m.fx_rate is not None else (m.amount / m.amount_usd if m.amount_usd else None)
    local = None if rate is None else expected_loss_usd * rate
    return {"currency": m.currency, "amount": m.amount, "amount_usd": round(m.amount_usd, 6), "fx_rate": m.fx_rate,
            "expected_loss_local": None if local is None else round(local, 4)}


@dataclass
class ScoringService:
    scorers: dict
    session_factory: sessionmaker
    fx: FxTable = field(default_factory=lambda: FxTable({"USD": 1.0}))

    def score(self, payment, client_id: str) -> ScoreResponse:
        t0 = time.perf_counter()
        rail = getattr(payment, "rail", "card")
        scorer = self.scorers.get(rail)
        if scorer is None:
            raise RailNotEnabled(rail)
        phash = payload_hash(payment)  # of the payment as submitted, before conversion
        existing = self._existing(payment.transaction_id, phash)
        if existing is not None:
            return existing

        # Risk is computed in USD. The card pipeline reads `amount`; rail scorers read `amount_usd`.
        money = self.fx.convert(payment)  # raises CurrencyError
        submitted = payment
        if rail == "card":
            if money.currency != "USD":
                payment = payment.model_copy(update={"amount": money.amount_usd, "currency": "USD"})
        elif payment.amount_usd is None:
            payment = payment.model_copy(update={"amount_usd": money.amount_usd})

        t = time.perf_counter()
        a = scorer.assess(payment)
        obs.SCORE_STAGE.labels(f"assess_{rail}").observe(time.perf_counter() - t)
        latency_ms = (time.perf_counter() - t0) * 1000

        t = time.perf_counter()
        case_id = None
        try:
            with write_guard(self.session_factory), self.session_factory() as s, s.begin():
                s.add(Transaction(id=payment.transaction_id, client_id=client_id, rail=rail,
                                  payload=submitted.model_dump(mode="json"), payload_hash=phash,
                                  event_time=payment.event_time, amount=money.amount_usd, currency=money.currency,
                                  amount_local=money.amount, **a.keys))
                s.flush()  # surface a concurrent duplicate as IntegrityError before the other inserts
                s.add(DecisionRecord(transaction_id=payment.transaction_id, decision=a.decision.value,
                                     fraud_probability=a.p, raw_score=a.raw, expected_loss=a.expected_loss,
                                     model_version=a.model_version, reasons=[r.model_dump() for r in a.reasons],
                                     rules=a.rules, actions=a.actions, features=clean_json(a.features),
                                     explanation=a.explanation, shadow=a.shadow, latency_ms=latency_ms))
                event = {"transaction_id": payment.transaction_id, "rail": rail, "decision": a.decision.value,
                         "fraud_probability": a.p, "amount": money.amount_usd, "currency": money.currency,
                         "amount_local": money.amount, "model_version": a.model_version,
                         "shadow": a.shadow, "degraded": a.degraded, "actions": a.actions}
                enqueue(s, "decision.made", payment.transaction_id, event)
                if a.decision != Decision.APPROVE:
                    case = Case(transaction_id=payment.transaction_id, priority=a.expected_loss, decision=a.decision.value)
                    s.add(case)
                    s.flush()
                    case_id = case.id
                    enqueue(s, "case.created", case.id, {"case_id": case.id, **event})
        except IntegrityError:
            # Lost a race with a concurrent request carrying the same transaction_id.
            existing = self._existing(payment.transaction_id, phash)
            if existing is None:
                raise
            return existing
        obs.SCORE_STAGE.labels("persist").observe(time.perf_counter() - t)

        obs.DECISIONS.labels(a.decision.value, a.model_version).inc()
        obs.FRAUD_PROB.observe(a.p)
        return ScoreResponse(transaction_id=payment.transaction_id, decision=a.decision,
                             fraud_probability=round(a.p, 6), expected_loss=round(a.expected_loss, 4),
                             reasons=a.reasons, model_version=a.model_version,
                             rules_triggered=[r for r in a.rules if not r.startswith("shadow:")], case_id=case_id,
                             latency_ms=round((time.perf_counter() - t0) * 1000, 3), degraded=a.degraded,
                             rail=rail, required_actions=a.actions, **_money(money, a.expected_loss))

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
                             case_id=case_id, latency_ms=dec.latency_ms, idempotent_replay=True,
                             rail=tx.rail or "card", required_actions=dec.actions or [],
                             **_money(stored_money(tx), dec.expected_loss))
