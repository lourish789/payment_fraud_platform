"""Proof-of-payment verification (computer vision): OCR -> ledger reconciliation -> image forensics."""

import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from payguard import observability as obs
from payguard.api.deps import PageParams, container, locale_of, page_params, require, require_any
from payguard.api.dto import Page, ReceiptOut, ReceiptSummary
from payguard.api.errors import ApiError, not_found
from payguard.db.models import Case, Receipt, Transaction
from payguard.db.session import write_guard
from payguard.services import localize, queries

router = APIRouter(prefix="/receipts", tags=["vision"])
MAX_RECEIPT_BYTES = 8 * 1024 * 1024


@router.post("/verify")
def verify_receipt(request: Request, file: UploadFile = File(...), claimed_reference: Optional[str] = Form(None),
                   case_id: Optional[str] = Form(None), _=Depends(require_any("merchant", "analyst"))):
    """Verify a proof-of-payment screenshot (png/jpeg/webp, max 8 MB). Optionally attach it to a case."""
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise ApiError(415, "unsupported_media_type", "png, jpeg or webp only")
    data = file.file.read(MAX_RECEIPT_BYTES + 1)
    if len(data) > MAX_RECEIPT_BYTES:
        raise ApiError(413, "too_large", "max 8 MB")
    c = container(request)
    if case_id:
        with c.session_factory() as s:
            if s.get(Case, case_id) is None:
                raise not_found("case")

    def ledger(reference: str) -> dict | None:
        with c.session_factory() as s:
            tx = s.get(Transaction, reference)
            if tx is None:
                return None
            # Receipts show the amount the customer actually paid, in their currency, not our USD base.
            local = tx.amount_local if tx.amount_local is not None else tx.payload.get("amount", tx.amount)
            return {"amount": local, "time": tx.event_time,
                    "currency": tx.currency or tx.payload.get("currency") or "USD"}

    try:
        result = c.verifier.verify(data, ledger, claimed_reference)
    except Exception as e:  # undecodable image etc.
        raise ApiError(422, "unprocessable_image", f"could not process image: {type(e).__name__}")
    obs.RECEIPTS.labels(result["verdict"]).inc()
    with write_guard(c.session_factory), c.session_factory() as s, s.begin():
        r = Receipt(case_id=case_id, claimed_reference=claimed_reference, sha256=hashlib.sha256(data).hexdigest(),
                    verdict=result["verdict"], result=result)
        s.add(r)
        s.flush()
        result["receipt_id"] = r.id
    return localize.receipt_result(result, locale_of(request))


@router.get("", response_model=Page[ReceiptSummary])
def list_receipts(request: Request, page: PageParams = Depends(page_params), verdict: Optional[str] = None,
                  case_id: Optional[str] = None, _=Depends(require("analyst"))):
    items, total = queries.list_receipts(container(request).session_factory, page.limit, page.offset, verdict, case_id)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{receipt_id}", response_model=ReceiptOut)
def get_receipt(receipt_id: str, request: Request, _=Depends(require("analyst"))):
    with container(request).session_factory() as s:
        r = s.get(Receipt, receipt_id)
        if r is None:
            raise not_found("receipt")
        out = queries.receipt_dict(r, full=True)
        return {**out, "result": localize.receipt_result(out["result"], locale_of(request))}
