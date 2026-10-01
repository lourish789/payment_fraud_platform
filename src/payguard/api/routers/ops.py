"""Liveness, readiness and Prometheus metrics (unauthenticated, at the root, for load balancers and scrapers)."""

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from payguard.api.deps import container
from payguard.services.admin import health

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz():
    """The process is up."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, response: Response):
    """Ready to score: model loaded, feature store and database reachable. 503 otherwise."""
    c = container(request)
    checks = health(c)
    checks.pop("workers", None)
    ok = all(checks.values())
    response.status_code = 200 if ok else 503
    return {"ready": ok, "checks": checks, "model_version": c.models.champion.version if c.models.champion else None}


@router.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
