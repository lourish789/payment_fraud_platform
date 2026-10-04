"""Who am I, and my profile: the console exchanges an API key for the caller's identity, capabilities and
preferences (language, display currency). Every role can read and change its own preferences."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from payguard.api.deps import container, locale_of, principal
from payguard.api.dto import Me, Preferences
from payguard.api.errors import ApiError
from payguard.api.security import Principal, validate_preferences
from payguard.db.models import ApiClient
from payguard.db.session import write_guard
from payguard.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

PERMISSIONS = {
    "merchant": ["payments:score", "receipts:verify"],
    "analyst": ["transactions:read", "cases:read", "cases:resolve", "investigations:run", "labels:write",
                "receipts:read", "monitoring:read"],
}
PERMISSIONS["admin"] = sorted({*PERMISSIONS["merchant"], *PERMISSIONS["analyst"], "models:read", "models:promote",
                               "admin:read", "clients:manage", "audit:read"})


def _me(p: Principal, request: Request) -> Me:
    return Me(client_id=p.client_id, name=p.name, role=p.role, permissions=PERMISSIONS[p.role],
              preferences=Preferences(locale=p.locale, currency=p.currency), locale=locale_of(request))


@router.get("/me", response_model=Me)
def me(request: Request, p: Principal = Depends(principal)):
    return _me(p, request)


class PreferencesIn(BaseModel):
    """Fields left out are unchanged; an explicit null clears the preference."""
    locale: Optional[str] = Field(None, max_length=10, examples=["yo"])
    currency: Optional[str] = Field(None, max_length=10, examples=["NGN"])


@router.patch("/me/preferences", response_model=Me)
def update_preferences(body: PreferencesIn, request: Request, p: Principal = Depends(principal)):
    """Set the caller's language and display currency. They follow the key: every console session and every
    API response for this key uses them (a request's ?lang= still overrides the language for that request)."""
    c = container(request)
    sent = body.model_fields_set
    try:
        locale, currency = validate_preferences(body.locale, body.currency, c.fx)
    except ValueError as e:
        raise ApiError(422, "invalid_preference", str(e))
    with write_guard(c.session_factory), c.session_factory() as s, s.begin():
        cli = s.get(ApiClient, p.client_id)
        if "locale" in sent:
            cli.locale = locale
        if "currency" in sent:
            cli.display_currency = currency
        audit.record(s, p, "client.preferences", p.client_id, {"locale": cli.locale, "currency": cli.display_currency})
        updated = Principal(p.client_id, p.name, p.role, cli.locale, cli.display_currency)
    c.auth.invalidate()
    request.state.principal = updated
    return _me(updated, request)
