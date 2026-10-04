"""Relational schema (SQLite in dev, Postgres in docker-compose). JSON columns hold payload snapshots;
anything queried or joined on is a real column with an index."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def clean_json(obj):
    """JSON-safe copy: NaN/inf -> None (Postgres JSONB rejects NaN)."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    return obj


class Base(DeclarativeBase):
    pass


class ApiClient(Base):
    __tablename__ = "api_clients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cli"))
    name: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20))  # merchant | analyst | admin
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    key_prefix: Mapped[str | None] = mapped_column(String(12), nullable=True)  # shown in the console, not secret
    # Profile: language for human-readable API text and the console, and the currency amounts are shown in.
    # None = not chosen (the request's Accept-Language, then the deployment default, decide).
    locale: Mapped[str | None] = mapped_column(String(10), nullable=True)
    display_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("api_clients.id"), index=True)
    rail: Mapped[str] = mapped_column(String(20), default="card", index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    amount: Mapped[float] = mapped_column(Float)  # USD (the scoring base), so aggregates never mix currencies
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)  # as submitted
    amount_local: Mapped[float | None] = mapped_column(Float, nullable=True)  # as submitted, in `currency`
    bin_key: Mapped[str | None] = mapped_column(String(32), index=True)
    customer_key: Mapped[str | None] = mapped_column(String(32), index=True)
    device_key: Mapped[str | None] = mapped_column(String(32), index=True)
    counterparty_key: Mapped[str | None] = mapped_column(String(32), index=True)  # payee / wallet / address
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Covering index for time-windowed dashboard aggregates: the rows themselves are wide (JSON payload),
    # so scanning them for counts is ~4x slower than an index-only scan.
    __table_args__ = (Index("ix_transactions_dashboard", "event_time", "id", "rail", "amount"),)


class DecisionRecord(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(10), index=True)
    fraud_probability: Mapped[float] = mapped_column(Float)
    raw_score: Mapped[float] = mapped_column(Float)
    expected_loss: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(40), index=True)
    reasons: Mapped[list] = mapped_column(JSON)
    rules: Mapped[list] = mapped_column(JSON)
    actions: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e.g. freeze_funds, hold_payment
    features: Mapped[dict] = mapped_column(JSON)  # exact serving-time snapshot (skew audits, retraining)
    explanation: Mapped[list | None] = mapped_column(JSON, nullable=True)  # TreeSHAP, computed async
    shadow: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (Index("ix_decisions_dashboard", "transaction_id", "decision", "fraud_probability", "latency_ms"),)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("case"))
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|resolved
    priority: Mapped[float] = mapped_column(Float, index=True)  # expected loss in $
    decision: Mapped[str] = mapped_column(String(10))
    resolution: Mapped[str | None] = mapped_column(String(10), nullable=True)  # fraud|legit
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("inv"))
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued|running|done|failed
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Label(Base):
    """Ground truth arrives late (analyst resolution now, chargebacks weeks later)."""
    __tablename__ = "labels"
    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_fraud: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20))  # analyst|chargeback|backfill
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rcpt"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    claimed_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    """Transactional outbox: written in the same DB transaction as the state change it announces,
    then relayed to the event bus. Guarantees no event is lost and none is emitted for a rolled-back
    write (at-least-once delivery; consumers are idempotent)."""
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "id"),)


class AuditLog(Base):
    """Append-only record of state-changing operator actions (who did what to which resource, when).
    Scoring is not audited here: every decision already has its own immutable DecisionRecord."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    actor_name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40), index=True)  # e.g. case.resolve, client.create
    resource: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
