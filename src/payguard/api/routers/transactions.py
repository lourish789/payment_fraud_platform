"""Transactions and their decisions (analyst read model)."""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request

from payguard.api.deps import PageParams, container, page_params, require
from payguard.api.dto import Page, TransactionDetail, TransactionSummary
from payguard.api.errors import not_found
from payguard.services import queries

router = APIRouter(prefix="/transactions", tags=["transactions"])
Rail = Literal["card", "bank_transfer", "mobile_money", "crypto"]


@router.get("", response_model=Page[TransactionSummary])
def list_transactions(request: Request, page: PageParams = Depends(page_params), rail: Optional[Rail] = None,
                      decision: Optional[Literal["approve", "review", "decline"]] = None,
                      label: Optional[Literal["fraud", "legit", "unlabelled"]] = None,
                      q: Optional[str] = Query(None, max_length=64, description="Transaction id prefix"),
                      start: Optional[datetime] = Query(None, alias="from"),
                      end: Optional[datetime] = Query(None, alias="to"),
                      min_amount: Optional[float] = Query(None, ge=0), client_id: Optional[str] = None,
                      _=Depends(require("analyst"))):
    """Newest first (by event time). Filters combine with AND."""
    items, total = queries.list_transactions(container(request).session_factory, page.limit, page.offset, rail,
                                             decision, label, q, start, end, min_amount, client_id)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{transaction_id}", response_model=TransactionDetail)
def get_transaction(transaction_id: str, request: Request, _=Depends(require("analyst"))):
    tx = queries.get_transaction(container(request).session_factory, transaction_id)
    if tx is None:
        raise not_found("transaction")
    return tx
