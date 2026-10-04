"""Response models (the API's read contract). Every route declares one, so the OpenAPI document is
complete and the web console's TypeScript types (frontend/src/api/types.ts) mirror it one to one.

Request models for scoring live in payguard.schemas (they are also the event schema)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

from payguard.schemas import ReasonCode

T = TypeVar("T")
Role = Literal["merchant", "analyst", "admin"]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---- auth & profile ------------------------------------------------------------------------------
class Preferences(BaseModel):
    locale: Optional[str] = Field(None, description="Language of human-readable text: en, fr, yo, ha, ig, pcm. "
                                                    "null = follow Accept-Language")
    currency: Optional[str] = Field(None, description="Currency the console shows amounts in, e.g. USD or NGN")


class Me(BaseModel):
    client_id: str
    name: str
    role: Role
    permissions: list[str] = Field(description="Capabilities the console uses to show or hide features")
    preferences: Preferences = Preferences()
    locale: str = Field("en", description="The language this response was rendered in")


class LocaleOut(BaseModel):
    code: str
    name: str


class CurrencyInfo(BaseModel):
    base: str
    rates: dict[str, float] = Field(description="Units of each currency per 1 USD")
    display: list[str]
    as_of: Optional[str] = None
    source: Optional[str] = None


class Meta(BaseModel):
    locales: list[LocaleOut]
    default_locale: str
    currencies: CurrencyInfo


# ---- transactions & decisions ----------------------------------------------------------------------
class DecisionOut(BaseModel):
    decision: str
    fraud_probability: float
    expected_loss: float
    model_version: str
    reasons: list[ReasonCode]
    rules: list[str]
    actions: list[str] = []
    shadow: Optional[dict] = None
    latency_ms: float
    created_at: datetime


class LabelOut(BaseModel):
    is_fraud: bool
    source: str
    created_at: Optional[datetime] = None


class TransactionSummary(BaseModel):
    transaction_id: str
    rail: str
    amount: float = Field(description="USD, the currency risk is computed in")
    currency: Optional[str] = Field(None, description="Currency as submitted")
    amount_local: Optional[float] = Field(None, description="Amount as submitted, in `currency`")
    event_time: datetime
    decision: Optional[str] = None
    fraud_probability: Optional[float] = None
    model_version: Optional[str] = None
    case_id: Optional[str] = None
    label: Optional[bool] = None
    created_at: datetime


class Money(BaseModel):
    currency: str = Field(description="As submitted")
    amount: float = Field(description="As submitted, in `currency`")
    amount_usd: float = Field(description="What risk was computed on")


class TransactionDetail(BaseModel):
    transaction_id: str
    rail: str
    money: Optional[Money] = None
    transaction: dict = Field(description="The payload exactly as submitted")
    decision: Optional[DecisionOut] = None
    label: Optional[LabelOut] = None
    case_id: Optional[str] = None


# ---- cases & investigations ------------------------------------------------------------------------
class AgentSummary(BaseModel):
    status: str
    recommendation: Optional[str] = None
    confidence: Optional[float] = None


class CaseSummary(BaseModel):
    case_id: str
    transaction_id: str
    rail: str
    status: str
    decision: str
    priority: float = Field(description="Expected loss in USD; the queue is ordered by it")
    amount: float = Field(description="USD")
    currency: Optional[str] = None
    amount_local: Optional[float] = None
    resolution: Optional[str] = None
    created_at: datetime
    agent: Optional[AgentSummary] = None


class InvestigationOut(BaseModel):
    investigation_id: str
    case_id: str
    status: str
    provider: Optional[str] = None
    model: Optional[str] = None
    recommendation: Optional[str] = None
    confidence: Optional[float] = None
    report: Optional[dict] = None
    error: Optional[str] = None
    tokens: dict
    created_at: datetime
    finished_at: Optional[datetime] = None
    trace: Optional[list] = None


class ReceiptSummary(BaseModel):
    receipt_id: str
    case_id: Optional[str] = None
    claimed_reference: Optional[str] = None
    verdict: str
    created_at: datetime


class ReceiptOut(ReceiptSummary):
    sha256: str
    result: dict


class CaseDetail(BaseModel):
    case_id: str
    status: str
    decision: str
    priority: float
    resolution: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    transaction_id: str
    rail: str
    money: Optional[Money] = None
    transaction: dict
    model: Optional[DecisionOut] = None
    explanation: Optional[list[dict]] = None
    investigations: list[InvestigationOut]
    receipts: list[ReceiptSummary]


class Resolved(BaseModel):
    case_id: str
    status: str
    resolution: str


class Queued(BaseModel):
    investigation_id: str
    status: str


class Accepted(BaseModel):
    accepted: int
    unknown: list[str] = Field(default_factory=list,
                               description="transaction_ids we have no record of; not stored (at most 100 listed)")
    unknown_count: int = 0


# ---- admin -------------------------------------------------------------------------------------------
class ClientOut(BaseModel):
    client_id: str
    name: str
    role: Role
    active: bool
    key_prefix: Optional[str] = None
    created_at: datetime
    revoked_at: Optional[datetime] = None
    preferences: Preferences = Preferences()


class ClientCreated(ClientOut):
    api_key: str = Field(description="Shown once. Only its SHA-256 is stored.")


class AuditOut(BaseModel):
    id: int
    actor_id: str
    actor_name: str
    action: str
    resource: str
    details: Optional[dict] = None
    created_at: datetime


class Window(BaseModel):
    start: Optional[datetime] = Field(None, alias="from")
    end: Optional[datetime] = Field(None, alias="to")
    anchored_to: Literal["now", "latest_event", "explicit"]

    model_config = {"populate_by_name": True}


class Overview(BaseModel):
    window: Window
    transactions: dict[str, Any]
    by_decision: dict[str, int]
    by_rail: list[dict[str, Any]]
    latency_ms: dict[str, Optional[float]]
    cases: dict[str, Any]
    labels: dict[str, Any]
    agent: dict[str, Any]
    receipts: dict[str, int]
    events: dict[str, Any]
    models: dict[str, Any]
    health: dict[str, bool]


class TimeseriesPoint(BaseModel):
    t: str
    total: int
    approve: int = 0
    review: int = 0
    decline: int = 0
    amount: float = 0.0
    flagged_amount: float = 0.0


class Timeseries(BaseModel):
    window: Window
    bucket: Literal["hour", "day"]
    series: list[TimeseriesPoint]
    by_rail: dict[str, list[dict[str, Any]]]


class RuleOut(BaseModel):
    rail: str
    id: str
    description: str
    action: str
    mode: str
    conditions: list[dict]
    hits: int = Field(description="Hits among the most recent decisions sampled")


class RulesReport(BaseModel):
    sampled_decisions: int
    rules: list[RuleOut]


class RailOut(BaseModel):
    rail: str
    enabled: bool
    engine: Literal["model", "scorecard"]
    version: Optional[str] = None
    rules: int
    details: dict[str, Any]
    stats: dict[str, Any]


class EventsReport(BaseModel):
    topics: list[dict[str, Any]]
    recent: Page[dict]


class SystemInfo(BaseModel):
    version: str
    started_at: datetime
    uptime_s: float
    components: dict[str, Any]
    settings: dict[str, Any]
