"""Admin API behind the admin dashboard: system-wide analytics, rails and rules, the event pipeline,
API clients (keys), the audit log and component status. Every route requires the admin role."""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from payguard.api.deps import PageParams, container, locale_of, page_params, require
from payguard.api.dto import (AuditOut, ClientCreated, ClientOut, EventsReport, Overview, Page, RailOut, RulesReport,
                              SystemInfo, Timeseries)
from payguard.api.errors import ApiError, not_found
from payguard.api.security import Principal, hash_key, new_key, validate_preferences
from payguard.i18n import translate
from payguard.db.models import ApiClient, utcnow
from payguard.db.session import write_guard
from payguard.services import admin as svc
from payguard.services import audit, queries

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require("admin"))])
WindowName = Literal["24h", "7d", "30d", "90d", "all"]
Anchor = Literal["latest_event", "now"]


def _window(request: Request, window: WindowName, anchor: Anchor, start, end) -> dict:
    if start and end and start >= end:
        raise ApiError(400, "invalid_window", "'from' must be before 'to'")
    return svc.resolve_window(container(request).session_factory, window, anchor, start, end)


# ---- analytics ---------------------------------------------------------------------------------------
@router.get("/overview", response_model=Overview)
def overview(request: Request, window: WindowName = "30d", anchor: Anchor = "now",
             start: Optional[datetime] = Query(None, alias="from"), end: Optional[datetime] = Query(None, alias="to")):
    """Everything on one page: traffic and decisions per rail, latency, case queue, labels and live
    precision/recall, agent outcomes and analyst agreement, receipts, outbox backlog, models, health."""
    return svc.overview(container(request), _window(request, window, anchor, start, end))


@router.get("/timeseries", response_model=Timeseries)
def timeseries(request: Request, window: WindowName = "30d", anchor: Anchor = "now",
               bucket: Literal["hour", "day"] = "day",
               start: Optional[datetime] = Query(None, alias="from"), end: Optional[datetime] = Query(None, alias="to")):
    """Decisions and amounts per time bucket, overall and per rail."""
    w = _window(request, window, anchor, start, end)
    if bucket == "hour" and (w["from"] is None or (w["to"] - w["from"]).days > 31):
        raise ApiError(400, "invalid_window", "hourly buckets need a window of 31 days or less")
    return svc.timeseries(container(request).session_factory, w, bucket)


@router.get("/rails", response_model=list[RailOut])
def rails(request: Request):
    """Each payment rail: enabled, engine (model or scorecard), version, configuration and traffic."""
    loc = locale_of(request)
    out = svc.rails_report(container(request))
    for r in out:
        if isinstance(r.get("details", {}).get("weights"), list):
            r["details"]["weights"] = [{**w, "reason": translate(w.get("reason"), loc)} for w in r["details"]["weights"]]
    return out


@router.get("/rules", response_model=RulesReport)
def rules(request: Request):
    """Every rule on every rail with its mode (enforce/shadow) and recent hit count."""
    loc = locale_of(request)
    rep = svc.rules_report(container(request))
    return {**rep, "rules": [{**r, "description": translate(r["description"], loc)} for r in rep["rules"]]}


@router.get("/events", response_model=EventsReport)
def events(request: Request, page: PageParams = Depends(page_params),
           topic: Optional[Literal["decision.made", "case.created", "label.recorded", "investigation.requested"]] = None,
           pending: Optional[bool] = None):
    """Outbox health per topic (backlog, oldest pending age) and the most recent events."""
    sf = container(request).session_factory
    items, total = queries.list_events(sf, page.limit, page.offset, topic, pending)
    return {"topics": svc.events_topics(sf), "recent": Page(items=items, total=total, limit=page.limit, offset=page.offset)}


@router.get("/system", response_model=SystemInfo)
def system(request: Request):
    """Version, uptime, component types and health, and the non-secret runtime settings."""
    return svc.system_info(container(request))


# ---- API clients -----------------------------------------------------------------------------------------
def _client(c: ApiClient) -> dict:
    return {"client_id": c.id, "name": c.name, "role": c.role, "active": c.active, "key_prefix": c.key_prefix,
            "created_at": c.created_at, "revoked_at": c.revoked_at,
            "preferences": {"locale": c.locale, "currency": c.display_currency}}


@router.get("/clients", response_model=Page[ClientOut])
def list_clients(request: Request, page: PageParams = Depends(page_params),
                 role: Optional[Literal["merchant", "analyst", "admin"]] = None, active: Optional[bool] = None):
    stmt = select(ApiClient)
    if role:
        stmt = stmt.where(ApiClient.role == role)
    if active is not None:
        stmt = stmt.where(ApiClient.active.is_(active))
    with container(request).session_factory() as s:
        rows, total = queries._page(s, stmt.order_by(ApiClient.created_at.desc()), page.limit, page.offset)
    return Page(items=[_client(c) for (c,) in rows], total=total, limit=page.limit, offset=page.offset)


class ClientIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    role: Literal["merchant", "analyst", "admin"]
    locale: Optional[str] = Field(None, max_length=10, description="Profile language: en, fr, yo, ha, ig, pcm")
    currency: Optional[str] = Field(None, max_length=10, description="Profile display currency, e.g. USD or NGN")


@router.post("/clients", response_model=ClientCreated, status_code=201)
def create(body: ClientIn, request: Request, p: Principal = Depends(require("admin"))):
    """Issue an API key. The key is returned once; only its hash is stored."""
    try:
        locale, currency = validate_preferences(body.locale, body.currency, container(request).fx)
    except ValueError as e:
        raise ApiError(422, "invalid_preference", str(e))
    key = new_key()
    sf = container(request).session_factory
    with write_guard(sf), sf() as s, s.begin():
        cli = ApiClient(name=body.name, key_hash=hash_key(key), role=body.role, key_prefix=key[:10], locale=locale,
                        display_currency=currency)
        s.add(cli)
        s.flush()
        audit.record(s, p, "client.create", cli.id, {"name": body.name, "role": body.role, "locale": locale,
                                                     "currency": currency})
        return {**_client(cli), "api_key": key}


@router.post("/clients/{client_id}/revoke", response_model=ClientOut)
def revoke(client_id: str, request: Request, p: Principal = Depends(require("admin"))):
    """Deactivate a key immediately. Revocation is permanent: issue a new key instead of reactivating."""
    if client_id == p.client_id:
        raise ApiError(409, "self_revoke", "you cannot revoke the key you are using")
    c = container(request)
    with write_guard(c.session_factory), c.session_factory() as s, s.begin():
        cli = s.get(ApiClient, client_id)
        if cli is None:
            raise not_found("client")
        if cli.active:
            cli.active, cli.revoked_at = False, utcnow()
            audit.record(s, p, "client.revoke", client_id, {"name": cli.name, "role": cli.role})
        out = _client(cli)
    c.auth.invalidate()
    return out


# ---- audit -------------------------------------------------------------------------------------------------
@router.get("/audit", response_model=Page[AuditOut])
def audit_log(request: Request, page: PageParams = Depends(page_params),
              action: Optional[str] = Query(None, max_length=40, description="e.g. case.resolve, or a prefix: client"),
              actor_id: Optional[str] = None):
    items, total = queries.list_audit(container(request).session_factory, page.limit, page.offset, action, actor_id)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
