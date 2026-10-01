"""Model registry: champion/challenger aliases, version metadata and hot-swap promotion."""

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from payguard.api.deps import container, require
from payguard.api.errors import ApiError, not_found
from payguard.api.security import Principal
from payguard.db.session import write_guard
from payguard.services import audit

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models(request: Request, _=Depends(require("admin"))):
    """Aliases, promotion history (who/when/why) and every immutable version on disk."""
    reg = container(request).models.registry
    return {**reg.read(), "versions": reg.versions()}


@router.get("/{version}")
def get_model(version: str, request: Request, _=Depends(require("admin"))):
    """A version's training metadata: windows, parameters and the offline evaluation report."""
    reg = container(request).models.registry
    if version not in reg.versions():
        raise not_found("model version")
    return json.loads((reg.root / version / "metadata.json").read_text())


class Promote(BaseModel):
    alias: Literal["champion", "challenger"]
    version: Optional[str]
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/promote")
def promote(body: Promote, request: Request, p: Principal = Depends(require("admin"))):
    """Point an alias at a version (or clear the challenger). Serving reloads without a restart."""
    c = container(request)
    if body.alias == "champion" and body.version is None:
        raise ApiError(400, "invalid", "champion cannot be unset")
    try:
        c.models.registry.set_alias(body.alias, body.version, body.reason)
    except FileNotFoundError as e:
        raise ApiError(404, "not_found", str(e))
    c.models.reload()  # hot swap, no restart
    with write_guard(c.session_factory), c.session_factory() as s, s.begin():
        audit.record(s, p, "model.promote", body.alias, body.model_dump())
    return c.models.registry.read()
