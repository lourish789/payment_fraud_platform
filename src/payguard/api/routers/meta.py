"""What this deployment supports: languages and currencies. Public (no key), so the sign-in page can offer
the language picker before anyone has signed in; it holds no secrets."""

from fastapi import APIRouter, Request

from payguard.api.deps import container
from payguard.api.dto import Meta
from payguard.i18n import DEFAULT, LOCALES, normalize

router = APIRouter(prefix="/meta", tags=["auth"])


@router.get("", response_model=Meta)
def meta(request: Request):
    c = container(request)
    return Meta(locales=[{"code": k, "name": v} for k, v in LOCALES.items()],
                default_locale=normalize(c.settings.default_locale) or DEFAULT, currencies=c.fx.public())
