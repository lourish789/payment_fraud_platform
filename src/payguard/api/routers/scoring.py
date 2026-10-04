"""Synchronous decisioning. Idempotent on transaction_id: retries return the original decision."""

from fastapi import APIRouter, Depends, Request

from payguard.api.deps import container, locale_of, require
from payguard.api.errors import ApiError
from payguard.api.security import Principal
from payguard.currency import CurrencyError
from payguard.schemas import PaymentIn, ScoreResponse, TransactionIn
from payguard.services import localize
from payguard.services.scoring import IdempotencyConflict, RailNotEnabled

router = APIRouter(tags=["scoring"])


def _score(payment, request: Request, p: Principal) -> ScoreResponse:
    try:
        return localize.score(container(request).scoring.score(payment, p.client_id), locale_of(request))
    except CurrencyError as e:
        raise ApiError(422, e.code, e.message, {"currency": e.currency})
    except IdempotencyConflict:
        raise ApiError(409, "idempotency_conflict", "transaction_id already used with a different payload")
    except RailNotEnabled as e:
        raise ApiError(400, "rail_not_enabled", f"payment rail '{e}' is not enabled on this deployment")
    except RuntimeError as e:
        raise ApiError(503, "unavailable", str(e))


@router.post("/payments/score", response_model=ScoreResponse)
def score_payment(payment: PaymentIn, request: Request, p: Principal = Depends(require("merchant"))):
    """Score a payment on any rail: card, bank_transfer, mobile_money or crypto (select with `rail`).
    Every rail lands in the same case queue. `required_actions` says what to do beyond the decision
    (e.g. a crypto deposit cannot be declined on-chain, so a sanctions hit returns freeze_funds)."""
    return _score(payment, request, p)


@router.post("/transactions/score", response_model=ScoreResponse)
def score_card(txn: TransactionIn, request: Request, p: Principal = Depends(require("merchant"))):
    """Score a card transaction (the original card-only contract; equivalent to rail=card above)."""
    return _score(txn, request, p)
