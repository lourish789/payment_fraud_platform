"""Canonical API / event schema. Dataset adapters map raw sources into this; the model never sees raw
source column names, so swapping the data source does not touch serving code."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, Field, field_validator

SignalValue = Union[float, str, None]


class Card(BaseModel):
    bin: Optional[str] = Field(None, description="Issuer/BIN-like identifier (IEEE card1)")
    issuer: Optional[str] = None
    country_code: Optional[str] = None
    category_code: Optional[str] = None
    network: Optional[str] = Field(None, examples=["visa"])
    type: Optional[str] = Field(None, examples=["debit"])


class Billing(BaseModel):
    region: Optional[str] = None
    country: Optional[str] = None


class Device(BaseModel):
    type: Optional[str] = Field(None, examples=["mobile"])
    info: Optional[str] = None
    os: Optional[str] = None
    browser: Optional[str] = None
    screen: Optional[str] = None


class TransactionIn(BaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=64)
    event_time: datetime
    amount: float = Field(..., gt=0, lt=1e7)
    currency: str = "USD"
    product_code: Optional[str] = None
    card: Card = Card()
    billing: Billing = Billing()
    distance: Optional[float] = None
    distance_secondary: Optional[float] = None
    payer_email_domain: Optional[str] = None
    recipient_email_domain: Optional[str] = None
    device: Device = Device()
    # Upstream processor risk signals (IEEE-CIS C/D/M/V/id columns). Opaque to us, versioned by name.
    signals: dict[str, SignalValue] = Field(default_factory=dict)

    @field_validator("event_time")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @property
    def ts(self) -> float:
        return self.event_time.timestamp()


class Decision(str, Enum):
    APPROVE = "approve"
    REVIEW = "review"
    DECLINE = "decline"


class ReasonCode(BaseModel):
    code: str
    detail: str
    weight: float = 0.0


class ScoreResponse(BaseModel):
    transaction_id: str
    decision: Decision
    fraud_probability: float
    expected_loss: float
    reasons: list[ReasonCode]
    model_version: str
    rules_triggered: list[str]
    case_id: Optional[str] = None
    latency_ms: float
    idempotent_replay: bool = False
    degraded: bool = False  # true when the online feature store was unavailable
