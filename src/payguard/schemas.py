"""Canonical API / event schema. Dataset adapters map raw sources into this; the model never sees raw
source column names, so swapping the data source does not touch serving code."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Optional, Union

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


class _RailBase(BaseModel):
    """Fields every payment rail shares. The rail-specific payloads below extend this."""

    transaction_id: str = Field(..., min_length=1, max_length=64)
    event_time: datetime
    amount: float = Field(..., gt=0, lt=1e9)
    currency: str = "USD"
    amount_usd: Optional[float] = Field(None, gt=0, description="Amount converted by the caller; defaults to amount")
    account_id: str = Field(..., min_length=1, max_length=128, description="Our customer's account")
    account_age_days: Optional[float] = Field(None, ge=0)
    device: Device = Device()

    @field_validator("event_time")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @property
    def ts(self) -> float:
        return self.event_time.timestamp()

    @property
    def usd(self) -> float:
        return self.amount_usd if self.amount_usd is not None else self.amount


class BankTransferIn(_RailBase):
    """Account-to-account push payment (NIP/Faster Payments/RTP/ACH/wire). Main risks: authorised push
    payment scams and mule accounts on the receiving side."""

    rail: Literal["bank_transfer"]
    beneficiary_account: str = Field(..., min_length=1, max_length=128)
    beneficiary_bank: Optional[str] = None
    beneficiary_name: Optional[str] = None
    channel: Optional[Literal["app", "web", "ussd", "branch", "api"]] = None
    scheme: Optional[str] = Field(None, examples=["NIP", "FPS", "RTP", "ACH", "SWIFT"])


class MobileMoneyIn(_RailBase):
    """Mobile-money wallet transaction. Main risks: SIM-swap account takeover, cash-out through agents, mules."""

    rail: Literal["mobile_money"]
    kind: Literal["p2p", "cash_out", "cash_in", "merchant", "airtime", "bill"]
    counterparty_wallet: str = Field(..., min_length=1, max_length=128)
    agent_id: Optional[str] = Field(None, description="Cash-out/cash-in agent")
    sim_swap_days: Optional[float] = Field(None, ge=0, description="Days since the MSISDN's last SIM swap")


class TravelRuleInfo(BaseModel):
    """FATF Recommendation 16 data exchanged with the counterparty VASP."""

    originator_name: Optional[str] = None
    originator_account: Optional[str] = None
    beneficiary_name: Optional[str] = None
    beneficiary_account: Optional[str] = None


class CryptoTransferIn(_RailBase):
    """On-chain deposit to, or withdrawal from, a customer's exchange account."""

    rail: Literal["crypto"]
    direction: Literal["deposit", "withdrawal"]
    asset: str = Field(..., examples=["BTC", "ETH", "USDT"])
    chain: str = Field(..., examples=["bitcoin", "ethereum", "tron"])
    counterparty_address: str = Field(..., min_length=10, max_length=128)
    tx_hash: Optional[str] = Field(None, description="On-chain transaction (known for deposits)")
    counterparty_vasp: Optional[str] = Field(None, description="Counterparty exchange, if hosted (not self-custody)")
    travel_rule: Optional[TravelRuleInfo] = None


class CardPaymentIn(TransactionIn):
    rail: Literal["card"] = "card"


PaymentIn = Annotated[Union[CardPaymentIn, BankTransferIn, MobileMoneyIn, CryptoTransferIn], Field(discriminator="rail")]
RAILS = ("card", "bank_transfer", "mobile_money", "crypto")


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
    rail: str = "card"
    # What the caller must do beyond approve/review/decline, e.g. a crypto deposit cannot be declined
    # (it is already on-chain), so a sanctions hit means "freeze_funds" + "file_sanctions_report".
    required_actions: list[str] = Field(default_factory=list)
