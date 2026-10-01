"""Agent investigations (read-only reports with a full tool trace)."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request

from payguard.api.deps import PageParams, container, page_params, require
from payguard.api.dto import InvestigationOut, Page
from payguard.api.errors import not_found
from payguard.db.models import Investigation
from payguard.services import queries

router = APIRouter(prefix="/investigations", tags=["agent"])


@router.get("", response_model=Page[InvestigationOut])
def list_investigations(request: Request, page: PageParams = Depends(page_params),
                        status: Optional[Literal["queued", "running", "done", "failed"]] = None,
                        recommendation: Optional[Literal["fraud", "legit", "escalate"]] = None,
                        case_id: Optional[str] = None, _=Depends(require("analyst"))):
    items, total = queries.list_investigations(container(request).session_factory, page.limit, page.offset,
                                               status, recommendation, case_id)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{inv_id}", response_model=InvestigationOut)
def get_investigation(inv_id: str, request: Request, include_trace: bool = False, _=Depends(require("analyst"))):
    with container(request).session_factory() as s:
        inv = s.get(Investigation, inv_id)
        if inv is None:
            raise not_found("investigation")
        return queries.investigation_dict(inv, include_trace)
