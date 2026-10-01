"""HTTP routes. Roles: merchant (score, upload receipts), analyst (cases, labels, monitoring), admin (models)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from payguard import observability as obs
from payguard.agent.service import enqueue_investigation
from payguard.api.security import Principal
from payguard.db.models import Case, DecisionRecord, Investigation, Label, Receipt, Transaction, utcnow
from payguard.events import enqueue
from payguard.monitoring import drift_report
from payguard.schemas import ScoreResponse, TransactionIn
from payguard.services.scoring import IdempotencyConflict

router = APIRouter()
MAX_RECEIPT_BYTES = 8 * 1024 * 1024


def _c(request: Request):
    return request.app.state.pg


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def principal(request: Request, authorization: str = Header(default="")) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise _err(401, "unauthenticated", "missing bearer token")
    p = _c(request).auth.resolve(authorization[7:].strip())
    if p is None:
        raise _err(401, "unauthenticated", "invalid API key")
    ok, retry_after = _c(request).limiter.allow(p.client_id)
    if not ok:
        obs.RATE_LIMITED.labels(p.name).inc()
        raise HTTPException(429, detail={"code": "rate_limited", "message": "slow down"},
                            headers={"Retry-After": f"{max(retry_after, 0.01):.2f}"})
    return p


def require(role: str):
    def dep(p: Principal = Depends(principal)) -> Principal:
        if not p.allows(role):
            raise _err(403, "forbidden", f"requires role {role}")
        return p
    return dep


# ---- scoring -------------------------------------------------------------------------------------
@router.post("/v1/transactions/score", response_model=ScoreResponse, tags=["scoring"])
def score(txn: TransactionIn, request: Request, p: Principal = Depends(require("merchant"))):
    """Score a transaction. Idempotent on transaction_id: retries return the original decision."""
    try:
        return _c(request).scoring.score(txn, p.client_id)
    except IdempotencyConflict:
        raise _err(409, "idempotency_conflict", "transaction_id already used with a different payload")
    except RuntimeError as e:
        raise _err(503, "unavailable", str(e))


@router.get("/v1/transactions/{transaction_id}", tags=["scoring"])
def get_transaction(transaction_id: str, request: Request, _: Principal = Depends(require("analyst"))):
    with _c(request).session_factory() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is None:
            raise _err(404, "not_found", "unknown transaction")
        dec = s.scalar(select(DecisionRecord).where(DecisionRecord.transaction_id == transaction_id))
        label = s.get(Label, transaction_id)
        return {"transaction": tx.payload, "decision": _decision(dec),
                "label": None if label is None else {"is_fraud": label.is_fraud, "source": label.source}}


def _decision(d: DecisionRecord | None) -> dict | None:
    if d is None:
        return None
    return {"decision": d.decision, "fraud_probability": d.fraud_probability, "expected_loss": d.expected_loss,
            "model_version": d.model_version, "reasons": d.reasons, "rules": d.rules, "shadow": d.shadow,
            "latency_ms": d.latency_ms, "created_at": d.created_at}


# ---- cases ----------------------------------------------------------------------------------------
@router.get("/v1/cases", tags=["cases"])
def list_cases(request: Request, status: Literal["open", "resolved"] = "open", limit: int = Query(50, le=200),
               _: Principal = Depends(require("analyst"))):
    """Analyst queue, highest expected loss first."""
    with _c(request).session_factory() as s:
        rows = s.execute(select(Case, Transaction.amount).join(Transaction, Transaction.id == Case.transaction_id)
                         .where(Case.status == status).order_by(Case.priority.desc()).limit(limit)).all()
        latest = {}
        ids = [c.id for c, _ in rows]
        if ids:
            for inv in s.scalars(select(Investigation).where(Investigation.case_id.in_(ids))
                                 .order_by(Investigation.created_at)).all():
                latest[inv.case_id] = inv
    return [{"case_id": c.id, "transaction_id": c.transaction_id, "decision": c.decision, "priority": round(c.priority, 2),
             "amount": amt, "created_at": c.created_at,
             "agent": None if c.id not in latest else {"status": latest[c.id].status,
                                                       "recommendation": latest[c.id].recommendation,
                                                       "confidence": latest[c.id].confidence}}
            for c, amt in rows]


@router.get("/v1/cases/{case_id}", tags=["cases"])
def get_case(case_id: str, request: Request, _: Principal = Depends(require("analyst"))):
    with _c(request).session_factory() as s:
        case = s.get(Case, case_id)
        if case is None:
            raise _err(404, "not_found", "unknown case")
        dec = s.scalar(select(DecisionRecord).where(DecisionRecord.transaction_id == case.transaction_id))
        invs = s.scalars(select(Investigation).where(Investigation.case_id == case_id)
                         .order_by(Investigation.created_at)).all()
        receipts = s.scalars(select(Receipt).where(Receipt.case_id == case_id)).all()
        explanation = _c(request).explainer.explain(case.transaction_id)
        return {"case_id": case.id, "status": case.status, "decision": case.decision, "priority": case.priority,
                "resolution": case.resolution, "transaction_id": case.transaction_id, "model": _decision(dec),
                "explanation": explanation,
                "investigations": [_inv(i) for i in invs],
                "receipts": [{"receipt_id": r.id, "verdict": r.verdict} for r in receipts]}


class Resolution(BaseModel):
    resolution: Literal["fraud", "legit"]
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/v1/cases/{case_id}/resolve", tags=["cases"])
def resolve_case(case_id: str, body: Resolution, request: Request, p: Principal = Depends(require("analyst"))):
    """Analyst decision. Also records the label that feeds retraining and live-precision monitoring."""
    with _c(request).session_factory() as s, s.begin():
        case = s.get(Case, case_id, with_for_update=True)
        if case is None:
            raise _err(404, "not_found", "unknown case")
        if case.status == "resolved":
            if case.resolution == body.resolution:
                return {"case_id": case_id, "status": "resolved", "resolution": case.resolution}
            raise _err(409, "already_resolved", f"case already resolved as {case.resolution}")
        case.status, case.resolution, case.resolution_note = "resolved", body.resolution, body.note
        case.resolved_by, case.resolved_at = p.client_id, utcnow()
        s.merge(Label(transaction_id=case.transaction_id, is_fraud=body.resolution == "fraud", source="analyst"))
        enqueue(s, "label.recorded", case.transaction_id,
                {"transaction_id": case.transaction_id, "is_fraud": body.resolution == "fraud", "source": "analyst"})
    return {"case_id": case_id, "status": "resolved", "resolution": body.resolution}


@router.post("/v1/cases/{case_id}/investigate", status_code=202, tags=["agent"])
def investigate(case_id: str, request: Request, _: Principal = Depends(require("analyst"))):
    c = _c(request)
    with c.session_factory() as s:
        if s.get(Case, case_id) is None:
            raise _err(404, "not_found", "unknown case")
    inv_id = enqueue_investigation(c.session_factory, case_id)
    if c.workers:
        c.workers.submit_investigation(inv_id)
    return {"investigation_id": inv_id, "status": "queued"}


@router.get("/v1/investigations/{inv_id}", tags=["agent"])
def get_investigation(inv_id: str, request: Request, include_trace: bool = False,
                      _: Principal = Depends(require("analyst"))):
    with _c(request).session_factory() as s:
        inv = s.get(Investigation, inv_id)
        if inv is None:
            raise _err(404, "not_found", "unknown investigation")
        return _inv(inv, include_trace)


def _inv(i: Investigation, include_trace: bool = False) -> dict:
    d = {"investigation_id": i.id, "case_id": i.case_id, "status": i.status, "provider": i.provider, "model": i.model,
         "recommendation": i.recommendation, "confidence": i.confidence, "report": i.report, "error": i.error,
         "tokens": {"input": i.input_tokens, "output": i.output_tokens}, "created_at": i.created_at,
         "finished_at": i.finished_at}
    if include_trace:
        d["trace"] = i.trace
    return d


# ---- labels (delayed ground truth, e.g. chargeback feed) ----------------------------------------
class LabelIn(BaseModel):
    transaction_id: str
    is_fraud: bool
    source: Literal["chargeback", "analyst", "backfill"] = "chargeback"


@router.post("/v1/labels", tags=["labels"])
def post_labels(labels: list[LabelIn], request: Request, _: Principal = Depends(require("analyst"))):
    if len(labels) > 5000:
        raise _err(413, "too_large", "max 5000 labels per request")
    with _c(request).session_factory() as s, s.begin():
        for lab in labels:
            s.merge(Label(transaction_id=lab.transaction_id, is_fraud=lab.is_fraud, source=lab.source))
            enqueue(s, "label.recorded", lab.transaction_id, lab.model_dump())
    return {"accepted": len(labels)}


# ---- receipts (computer vision) ------------------------------------------------------------------
@router.post("/v1/receipts/verify", tags=["vision"])
def verify_receipt(request: Request, file: UploadFile = File(...), claimed_reference: Optional[str] = Form(None),
                   case_id: Optional[str] = Form(None), _: Principal = Depends(require("merchant"))):
    """Verify a proof-of-payment screenshot: OCR -> ledger reconciliation -> image forensics."""
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise _err(415, "unsupported_media_type", "png, jpeg or webp only")
    data = file.file.read(MAX_RECEIPT_BYTES + 1)
    if len(data) > MAX_RECEIPT_BYTES:
        raise _err(413, "too_large", "max 8 MB")
    c = _c(request)

    def ledger(reference: str) -> dict | None:
        with c.session_factory() as s:
            tx = s.get(Transaction, reference)
            if tx is None:
                return None
            return {"amount": tx.amount, "time": tx.event_time, "currency": tx.payload.get("currency")}

    try:
        result = c.verifier.verify(data, ledger, claimed_reference)
    except Exception as e:  # undecodable image etc.
        raise _err(422, "unprocessable_image", f"could not process image: {type(e).__name__}")
    obs.RECEIPTS.labels(result["verdict"]).inc()
    with c.session_factory() as s, s.begin():
        if case_id and s.get(Case, case_id) is None:
            raise _err(404, "not_found", "unknown case")
        r = Receipt(case_id=case_id, claimed_reference=claimed_reference, sha256=hashlib.sha256(data).hexdigest(),
                    verdict=result["verdict"], result=result)
        s.add(r)
        s.flush()
        result["receipt_id"] = r.id
    return result


# ---- monitoring and models ---------------------------------------------------------------------
@router.get("/v1/monitoring/drift", tags=["monitoring"])
def drift(request: Request, since: Optional[datetime] = None, _: Principal = Depends(require("analyst"))):
    c = _c(request)
    if c.models.champion is None:
        raise _err(503, "unavailable", "no model")
    return drift_report(c.session_factory, c.models.champion, since=since)


@router.get("/v1/models", tags=["models"])
def models(request: Request, _: Principal = Depends(require("admin"))):
    c = _c(request)
    reg = c.models.registry.read()
    return {**reg, "versions": c.models.registry.versions()}


class Promote(BaseModel):
    alias: Literal["champion", "challenger"]
    version: Optional[str]
    reason: str = Field(..., min_length=3)


@router.post("/v1/models/promote", tags=["models"])
def promote(body: Promote, request: Request, _: Principal = Depends(require("admin"))):
    c = _c(request)
    if body.alias == "champion" and body.version is None:
        raise _err(400, "invalid", "champion cannot be unset")
    try:
        c.models.registry.set_alias(body.alias, body.version, body.reason)
    except FileNotFoundError as e:
        raise _err(404, "not_found", str(e))
    c.models.reload()  # hot swap, no restart
    return c.models.registry.read()


# ---- health --------------------------------------------------------------------------------------
@router.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
def readyz(request: Request, response: Response):
    c = _c(request)
    checks = {"model": c.models.champion is not None, "feature_store": c.store.ping()}
    try:
        with c.session_factory() as s:
            s.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    ok = all(checks.values())
    response.status_code = 200 if ok else 503
    return {"ready": ok, "checks": checks, "model_version": c.models.champion.version if c.models.champion else None}


@router.get("/metrics", tags=["ops"])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
