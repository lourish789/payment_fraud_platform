"""Analyst case queue: every review/decline on every rail becomes a case, ordered by expected loss."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from payguard.agent.service import enqueue_investigation
from payguard.api.deps import PageParams, container, page_params, require
from payguard.api.dto import CaseDetail, CaseSummary, Page, Queued, Resolved
from payguard.api.errors import ApiError, not_found
from payguard.api.security import Principal
from payguard.db.models import Case, Label, utcnow
from payguard.db.session import write_guard
from payguard.events import enqueue
from payguard.services import audit, queries

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("", response_model=Page[CaseSummary])
def list_cases(request: Request, page: PageParams = Depends(page_params),
               status: Literal["open", "resolved", "all"] = "open",
               rail: Optional[Literal["card", "bank_transfer", "mobile_money", "crypto"]] = None,
               decision: Optional[Literal["review", "decline"]] = None,
               resolution: Optional[Literal["fraud", "legit"]] = None, _=Depends(require("analyst"))):
    """Highest expected loss first."""
    items, total = queries.list_cases(container(request).session_factory, page.limit, page.offset, status, rail,
                                      decision, resolution)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, request: Request, _=Depends(require("analyst"))):
    c = container(request)
    case = queries.get_case(c.session_factory, case_id, c.explainer)
    if case is None:
        raise not_found("case")
    return case


class Resolution(BaseModel):
    resolution: Literal["fraud", "legit"]
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/{case_id}/resolve", response_model=Resolved)
def resolve_case(case_id: str, body: Resolution, request: Request, p: Principal = Depends(require("analyst"))):
    """Analyst decision. Also records the label that feeds retraining and live-precision monitoring.
    Idempotent for the same resolution; a conflicting second resolution is rejected (409)."""
    sf = container(request).session_factory
    with write_guard(sf), sf() as s, s.begin():
        case = s.get(Case, case_id, with_for_update=True)
        if case is None:
            raise not_found("case")
        if case.status == "resolved":
            if case.resolution == body.resolution:
                return Resolved(case_id=case_id, status="resolved", resolution=case.resolution)
            raise ApiError(409, "already_resolved", f"case already resolved as {case.resolution}")
        case.status, case.resolution, case.resolution_note = "resolved", body.resolution, body.note
        case.resolved_by, case.resolved_at = p.client_id, utcnow()
        s.merge(Label(transaction_id=case.transaction_id, is_fraud=body.resolution == "fraud", source="analyst"))
        enqueue(s, "label.recorded", case.transaction_id,
                {"transaction_id": case.transaction_id, "is_fraud": body.resolution == "fraud", "source": "analyst"})
        audit.record(s, p, "case.resolve", case_id, {"resolution": body.resolution, "note": body.note})
    return Resolved(case_id=case_id, status="resolved", resolution=body.resolution)


@router.post("/{case_id}/investigate", status_code=202, response_model=Queued, tags=["agent"])
def investigate(case_id: str, request: Request, p: Principal = Depends(require("analyst"))):
    """Queue an agent investigation (reuses an unfinished one for the same case). It runs on the
    workers' interactive lane, ahead of any auto-investigation backlog: delivered as an
    `investigation.requested` event, and handed straight to in-process workers when there are any."""
    c = container(request)
    with c.session_factory() as s:
        if s.get(Case, case_id) is None:
            raise not_found("case")
    inv_id = enqueue_investigation(c.session_factory, case_id, requested_by=p.client_id)
    if c.workers:
        c.workers.submit_investigation(inv_id, interactive=True)  # fast path; the event is the guarantee
    return Queued(investigation_id=inv_id, status="queued")
