"""Read side for the analyst and admin console: filtered, paginated listings and detail views.

Routers stay thin (HTTP in, DTO out); every query lives here so it can be tested without HTTP and
reused by other entry points. List queries return (rows, total) with the same filters applied to both.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from payguard.db.models import AuditLog, Case, DecisionRecord, Investigation, Label, OutboxEvent, Receipt, Transaction


def _page(s: Session, q: Select, limit: int, offset: int) -> tuple[list, int]:
    total = s.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    return s.execute(q.limit(limit).offset(offset)).all(), int(total or 0)


# ---- transactions ----------------------------------------------------------------------------------
def decision_dict(d: DecisionRecord | None) -> dict | None:
    if d is None:
        return None
    return {"decision": d.decision, "fraud_probability": d.fraud_probability, "expected_loss": d.expected_loss,
            "model_version": d.model_version, "reasons": d.reasons, "rules": d.rules, "actions": d.actions or [],
            "shadow": d.shadow, "latency_ms": d.latency_ms, "created_at": d.created_at}


def list_transactions(sf: sessionmaker, limit: int, offset: int, rail: str | None = None,
                      decision: str | None = None, label: str | None = None, q: str | None = None,
                      start: datetime | None = None, end: datetime | None = None,
                      min_amount: float | None = None, client_id: str | None = None) -> tuple[list[dict], int]:
    stmt = (select(Transaction, DecisionRecord, Case.id, Label.is_fraud)
            .outerjoin(DecisionRecord, DecisionRecord.transaction_id == Transaction.id)
            .outerjoin(Case, Case.transaction_id == Transaction.id)
            .outerjoin(Label, Label.transaction_id == Transaction.id))
    if rail:
        stmt = stmt.where(Transaction.rail == rail)
    if decision:
        stmt = stmt.where(DecisionRecord.decision == decision)
    if label == "fraud":
        stmt = stmt.where(Label.is_fraud.is_(True))
    elif label == "legit":
        stmt = stmt.where(Label.is_fraud.is_(False))
    elif label == "unlabelled":
        stmt = stmt.where(Label.transaction_id.is_(None))
    if q:
        stmt = stmt.where(Transaction.id.like(f"{q}%"))
    if start:
        stmt = stmt.where(Transaction.event_time >= start)
    if end:
        stmt = stmt.where(Transaction.event_time < end)
    if min_amount is not None:
        stmt = stmt.where(Transaction.amount >= min_amount)
    if client_id:
        stmt = stmt.where(Transaction.client_id == client_id)
    stmt = stmt.order_by(Transaction.event_time.desc(), Transaction.id.desc())
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
    return [{"transaction_id": tx.id, "rail": tx.rail or "card", "amount": tx.amount,
             "currency": tx.currency or (tx.payload or {}).get("currency") or "USD",
             "amount_local": tx.amount_local if tx.amount_local is not None else tx.amount, "event_time": tx.event_time,
             "decision": d.decision if d else None, "fraud_probability": d.fraud_probability if d else None,
             "model_version": d.model_version if d else None, "case_id": case_id, "label": is_fraud,
             "created_at": tx.created_at} for tx, d, case_id, is_fraud in rows], total


def money_dict(tx: Transaction) -> dict:
    p = tx.payload or {}
    return {"currency": tx.currency or p.get("currency") or "USD",
            "amount": tx.amount_local if tx.amount_local is not None else p.get("amount", tx.amount),
            "amount_usd": tx.amount}


def get_transaction(sf: sessionmaker, transaction_id: str) -> dict | None:
    with sf() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is None:
            return None
        dec = s.scalar(select(DecisionRecord).where(DecisionRecord.transaction_id == transaction_id))
        label = s.get(Label, transaction_id)
        case_id = s.scalar(select(Case.id).where(Case.transaction_id == transaction_id))
        return {"transaction_id": tx.id, "rail": tx.rail or "card", "money": money_dict(tx), "transaction": tx.payload,
                "decision": decision_dict(dec), "case_id": case_id,
                "label": None if label is None else {"is_fraud": label.is_fraud, "source": label.source,
                                                     "created_at": label.created_at}}


# ---- cases -------------------------------------------------------------------------------------------
def _latest_investigations(s: Session, case_ids: list[str]) -> dict[str, Investigation]:
    latest: dict[str, Investigation] = {}
    if case_ids:
        for inv in s.scalars(select(Investigation).where(Investigation.case_id.in_(case_ids))
                             .order_by(Investigation.created_at)).all():
            latest[inv.case_id] = inv
    return latest


def list_cases(sf: sessionmaker, limit: int, offset: int, status: str = "open", rail: str | None = None,
               decision: str | None = None, resolution: str | None = None) -> tuple[list[dict], int]:
    stmt = (select(Case, Transaction.amount, Transaction.rail, Transaction.currency, Transaction.amount_local)
            .join(Transaction, Transaction.id == Case.transaction_id))
    if status != "all":
        stmt = stmt.where(Case.status == status)
    if rail:
        stmt = stmt.where(Transaction.rail == rail)
    if decision:
        stmt = stmt.where(Case.decision == decision)
    if resolution:
        stmt = stmt.where(Case.resolution == resolution)
    stmt = stmt.order_by(Case.priority.desc(), Case.id)
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
        latest = _latest_investigations(s, [c.id for c, *_ in rows])
    return [{"case_id": c.id, "transaction_id": c.transaction_id, "rail": rail_ or "card", "status": c.status,
             "decision": c.decision, "priority": round(c.priority, 2), "amount": amount, "currency": currency or "USD",
             "amount_local": amount if amount_local is None else amount_local, "resolution": c.resolution,
             "created_at": c.created_at,
             "agent": None if c.id not in latest else {"status": latest[c.id].status,
                                                       "recommendation": latest[c.id].recommendation,
                                                       "confidence": latest[c.id].confidence}}
            for c, amount, rail_, currency, amount_local in rows], total


def investigation_dict(i: Investigation, include_trace: bool = False) -> dict:
    d = {"investigation_id": i.id, "case_id": i.case_id, "status": i.status, "provider": i.provider, "model": i.model,
         "recommendation": i.recommendation, "confidence": i.confidence, "report": i.report, "error": i.error,
         "tokens": {"input": i.input_tokens, "output": i.output_tokens}, "created_at": i.created_at,
         "finished_at": i.finished_at}
    if include_trace:
        d["trace"] = i.trace
    return d


def receipt_dict(r: Receipt, full: bool = False) -> dict:
    d = {"receipt_id": r.id, "case_id": r.case_id, "claimed_reference": r.claimed_reference, "verdict": r.verdict,
         "created_at": r.created_at}
    if full:
        d |= {"sha256": r.sha256, "result": r.result}
    return d


def get_case(sf: sessionmaker, case_id: str, explainer) -> dict | None:
    with sf() as s:
        case = s.get(Case, case_id)
        if case is None:
            return None
        tx = s.get(Transaction, case.transaction_id)
        dec = s.scalar(select(DecisionRecord).where(DecisionRecord.transaction_id == case.transaction_id))
        invs = s.scalars(select(Investigation).where(Investigation.case_id == case_id)
                         .order_by(Investigation.created_at)).all()
        receipts = s.scalars(select(Receipt).where(Receipt.case_id == case_id).order_by(Receipt.created_at)).all()
    return {"case_id": case.id, "status": case.status, "decision": case.decision, "priority": case.priority,
            "resolution": case.resolution, "resolution_note": case.resolution_note, "resolved_by": case.resolved_by,
            "resolved_at": case.resolved_at, "created_at": case.created_at, "transaction_id": case.transaction_id,
            "rail": tx.rail or "card", "money": money_dict(tx), "transaction": tx.payload, "model": decision_dict(dec),
            "explanation": explainer.explain(case.transaction_id),
            "investigations": [investigation_dict(i) for i in invs],
            "receipts": [receipt_dict(r) for r in receipts]}


# ---- investigations, receipts, labels, audit, events --------------------------------------------------
def list_investigations(sf: sessionmaker, limit: int, offset: int, status: str | None = None,
                        recommendation: str | None = None, case_id: str | None = None) -> tuple[list[dict], int]:
    stmt = select(Investigation)
    if status:
        stmt = stmt.where(Investigation.status == status)
    if recommendation:
        stmt = stmt.where(Investigation.recommendation == recommendation)
    if case_id:
        stmt = stmt.where(Investigation.case_id == case_id)
    stmt = stmt.order_by(Investigation.created_at.desc(), Investigation.id)
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
    return [investigation_dict(i) for (i,) in rows], total


def list_receipts(sf: sessionmaker, limit: int, offset: int, verdict: str | None = None,
                  case_id: str | None = None) -> tuple[list[dict], int]:
    stmt = select(Receipt)
    if verdict:
        stmt = stmt.where(Receipt.verdict == verdict)
    if case_id:
        stmt = stmt.where(Receipt.case_id == case_id)
    stmt = stmt.order_by(Receipt.created_at.desc(), Receipt.id)
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
    return [receipt_dict(r) for (r,) in rows], total


def list_labels(sf: sessionmaker, limit: int, offset: int, source: str | None = None,
                is_fraud: bool | None = None) -> tuple[list[dict], int]:
    stmt = select(Label)
    if source:
        stmt = stmt.where(Label.source == source)
    if is_fraud is not None:
        stmt = stmt.where(Label.is_fraud.is_(is_fraud))
    stmt = stmt.order_by(Label.created_at.desc(), Label.transaction_id)
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
    return [{"transaction_id": lab.transaction_id, "is_fraud": lab.is_fraud, "source": lab.source,
             "created_at": lab.created_at} for (lab,) in rows], total


def list_audit(sf: sessionmaker, limit: int, offset: int, action: str | None = None,
               actor_id: str | None = None) -> tuple[list[dict], int]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(or_(AuditLog.action == action, AuditLog.action.like(f"{action}.%")))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    stmt = stmt.order_by(AuditLog.id.desc())
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
    return [{"id": a.id, "actor_id": a.actor_id, "actor_name": a.actor_name, "action": a.action,
             "resource": a.resource, "details": a.details, "created_at": a.created_at} for (a,) in rows], total


def list_events(sf: sessionmaker, limit: int, offset: int, topic: str | None = None,
                pending: bool | None = None) -> tuple[list[dict[str, Any]], int]:
    stmt = select(OutboxEvent)
    if topic:
        stmt = stmt.where(OutboxEvent.topic == topic)
    if pending is True:
        stmt = stmt.where(OutboxEvent.published_at.is_(None))
    elif pending is False:
        stmt = stmt.where(OutboxEvent.published_at.is_not(None))
    stmt = stmt.order_by(OutboxEvent.id.desc())
    with sf() as s:
        rows, total = _page(s, stmt, limit, offset)
    return [{"id": e.id, "topic": e.topic, "key": e.key, "payload": e.payload, "created_at": e.created_at,
             "published_at": e.published_at} for (e,) in rows], total
