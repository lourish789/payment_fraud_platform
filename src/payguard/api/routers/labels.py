"""Delayed ground truth (chargeback feeds, backfills). Analyst resolutions also land here."""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field

from payguard.api.deps import PageParams, container, page_params, require
from payguard.api.dto import Accepted, Page
from payguard.api.errors import ApiError
from payguard.db.models import Label
from payguard.db.session import write_guard
from payguard.events import enqueue
from payguard.services import queries

router = APIRouter(prefix="/labels", tags=["labels"])
MAX_BATCH = 5000


class LabelIn(BaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=64)
    is_fraud: bool
    source: Literal["chargeback", "analyst", "backfill"] = "chargeback"


class LabelRow(BaseModel):
    transaction_id: str
    is_fraud: bool
    source: str
    created_at: Optional[datetime] = None


@router.get("", response_model=Page[LabelRow])
def list_labels(request: Request, page: PageParams = Depends(page_params),
                source: Optional[Literal["chargeback", "analyst", "backfill"]] = None,
                is_fraud: Optional[bool] = None, _=Depends(require("analyst"))):
    items, total = queries.list_labels(container(request).session_factory, page.limit, page.offset, source, is_fraud)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=Accepted)
def post_labels(request: Request, labels: list[LabelIn] = Body(...), _=Depends(require("analyst"))):
    """Upsert a batch of labels (max 5000). Each emits a label.recorded event."""
    if len(labels) > MAX_BATCH:
        raise ApiError(413, "too_large", f"max {MAX_BATCH} labels per request")
    sf = container(request).session_factory
    with write_guard(sf), sf() as s, s.begin():
        for lab in labels:
            s.merge(Label(transaction_id=lab.transaction_id, is_fraud=lab.is_fraud, source=lab.source))
            enqueue(s, "label.recorded", lab.transaction_id, lab.model_dump())
    return Accepted(accepted=len(labels))
