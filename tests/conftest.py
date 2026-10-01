from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from payguard.schemas import Billing, Card, Device, TransactionIn

T0 = datetime(2018, 5, 1, tzinfo=timezone.utc)


def make_txn(i: int, minutes: float = 0.0, amount: float = 50.0, card: str = "1111", device: str = "iOS",
             **kw) -> TransactionIn:
    return TransactionIn(
        transaction_id=kw.pop("transaction_id", f"t{i}"),
        event_time=T0 + timedelta(minutes=minutes),
        amount=amount,
        product_code="W",
        card=Card(bin=card, issuer="321", country_code="150", category_code="226", network="visa", type="debit"),
        billing=Billing(region="299", country="87"),
        payer_email_domain="gmail.com",
        device=Device(type="mobile", info=device, os="iOS 11", browser="safari", screen="1334x750"),
        signals={"C1": 1.0, "D1": 0.0, "M4": "M0"},
        **kw,
    )


@pytest.fixture
def txn_factory():
    return make_txn
