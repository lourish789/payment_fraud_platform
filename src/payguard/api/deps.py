"""Request dependencies: the service container, authentication, role checks and pagination."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Query, Request

from payguard import observability as obs
from payguard.api.errors import ApiError
from payguard.api.security import Principal


def container(request: Request):
    return request.app.state.pg


def principal(request: Request, authorization: str = Header(default="")) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "missing bearer token")
    c = container(request)
    p = c.auth.resolve(authorization[7:].strip())
    if p is None:
        raise ApiError(401, "unauthenticated", "invalid API key")
    ok, retry_after = c.limiter.allow(p.client_id)
    if not ok:
        obs.RATE_LIMITED.labels(p.name).inc()
        raise ApiError(429, "rate_limited", "slow down", headers={"Retry-After": f"{max(retry_after, 0.01):.2f}"})
    request.state.principal = p
    return p


def require(role: str):
    """Role gate. Roles are not hierarchical except admin: analysts can't score, merchants can't read cases."""

    def dep(p: Principal = Depends(principal)) -> Principal:
        if not p.allows(role):
            raise ApiError(403, "forbidden", f"requires role {role}")
        return p

    return dep


def require_any(*roles: str):
    def dep(p: Principal = Depends(principal)) -> Principal:
        if not any(p.allows(r) for r in roles):
            raise ApiError(403, "forbidden", f"requires one of roles {', '.join(roles)}")
        return p

    return dep


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=1_000_000)) -> PageParams:
    return PageParams(limit, offset)
