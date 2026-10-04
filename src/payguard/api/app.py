"""FastAPI application and composition root."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from payguard import __version__
from payguard import observability as obs
from payguard.api import errors
from payguard.api.deps import locale_of
from payguard.agent.runner import Provider, make_provider
from payguard.api.security import Authenticator, RedisTokenBucket, TokenBucket, ensure_admin
from payguard.config import Settings, get_settings
from payguard.currency import FxTable
from payguard.db.session import init_db, make_engine, make_session_factory
from payguard.events import EventBus, make_bus
from payguard.features.pipeline import FeaturePipeline
from payguard.features.store import FeatureStore, InMemoryFeatureStore, load_snapshot, make_store
from payguard.models.registry import Registry
from payguard.rules import RuleEngine
from payguard.services.explanations import Explainer
from payguard.services.scoring import ModelHolder, ScoringService
from payguard.vision.verify import ReceiptVerifier
from payguard.workers import WorkerPool

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    session_factory: object
    store: FeatureStore
    bus: EventBus
    models: ModelHolder
    rules: RuleEngine
    scoring: ScoringService
    auth: Authenticator
    limiter: object
    provider: Provider
    workers: WorkerPool | None
    verifier: ReceiptVerifier
    explainer: Explainer
    fx: FxTable


def build_scorers(settings: Settings, store: FeatureStore, models: ModelHolder, rules: RuleEngine) -> dict:
    """One scorer per enabled rail. Crypto intelligence is optional: without a trained intel store the
    crypto rail still screens sanctions and runs its behavioural scorecard (and says so in its version)."""
    from payguard.crypto.intel import AddressIntel, CounterpartyRisk
    from payguard.crypto.screening import SanctionsScreener
    from payguard.services.rails import CardScorer, CryptoScorer, ScorecardScorer

    scorers: dict = {}
    for rail in settings.rails_enabled:
        if rail == "card":
            scorers[rail] = CardScorer(FeaturePipeline(store), models, rules)
        elif rail in ("bank_transfer", "mobile_money"):
            scorers[rail] = ScorecardScorer(rail, store, settings.rails_config_dir, settings.travel_rule_threshold_usd)
        elif rail == "crypto":
            screener = SanctionsScreener.load(settings.sanctions_dir) if settings.sanctions_dir.exists()                 else SanctionsScreener({})
            intel = combiner = version = None
            champ = settings.crypto_dir / "champion.txt"
            if champ.exists():
                version = champ.read_text().strip()
                d = settings.crypto_dir / version
                if (d / "address_intel.db").exists() and (d / "combiner.json").exists():
                    intel, combiner = AddressIntel.open(d / "address_intel.db"), CounterpartyRisk.load(d / "combiner.json")
            if intel is None:
                log.warning("crypto rail running without address intelligence (no trained intel store)")
            scorers[rail] = CryptoScorer(store, settings.rails_config_dir, screener, intel, combiner,
                                         version if intel else None, settings.travel_rule_threshold_usd)
            log.info("crypto rail: %d sanctioned addresses, intel=%s", len(screener), version if intel else None)
        else:
            raise ValueError(f"unknown rail {rail}")
    return scorers


def build_container(settings: Settings, store: FeatureStore | None = None, bus: EventBus | None = None,
                    provider: Provider | None = None, start_workers: bool | None = None) -> Container:
    fx = FxTable.load(settings.currency_path, settings.fx_rates)
    engine = make_engine(settings.database_url)
    init_db(engine, fx)
    sf = make_session_factory(engine)
    if settings.bootstrap_admin_key and ensure_admin(sf, settings.bootstrap_admin_key):
        log.info("bootstrap admin client created from PAYGUARD_BOOTSTRAP_ADMIN_KEY")
    store = store or make_store(settings.redis_url)
    if settings.online_snapshot and isinstance(store, InMemoryFeatureStore) and len(store) == 0:
        header = load_snapshot(store, settings.online_snapshot)
        log.info("online store warmed from snapshot as_of=%s entities=%s", header["as_of"], header["entities"])
    bus = bus or make_bus(settings.redis_url)
    models = ModelHolder(Registry(settings.models_dir))
    rules = RuleEngine.from_yaml(settings.rules_path)
    provider = provider or make_provider(settings.agent_provider, settings.agent_model, settings.agent_max_steps,
                                         settings.agent_max_output_tokens)
    if settings.redis_url:
        import redis

        limiter = RedisTokenBucket(redis.Redis.from_url(settings.redis_url), settings.rate_limit_rps, settings.rate_limit_burst)
    else:
        limiter = TokenBucket(settings.rate_limit_rps, settings.rate_limit_burst)
    scorers = build_scorers(settings, store, models, rules)
    explainer = Explainer(sf, models.registry)
    run_workers = settings.run_workers_in_process if start_workers is None else start_workers
    workers = WorkerPool(sf, bus, provider, explainer, settings.agent_auto_investigate) if run_workers else None
    return Container(settings, sf, store, bus, models, rules,
                     ScoringService(scorers, sf, fx),
                     Authenticator(sf), limiter, provider, workers,
                     ReceiptVerifier(settings.artifacts_dir / "vision" / "thresholds.json"), explainer, fx)


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or await run_in_threadpool(build_container, get_settings())
        app.state.pg = c
        if c.workers:
            c.workers.start()
        log.info("payguard ready: champion=%s provider=%s",
                 c.models.champion.version if c.models.champion else None, c.provider.name)
        yield
        if c.workers:
            c.workers.stop()

    settings = container.settings if container else get_settings()
    app = FastAPI(title="PayGuard", version=__version__, lifespan=lifespan, openapi_tags=OPENAPI_TAGS,
                  description="Real-time multi-rail payment fraud scoring, case management, investigation agent, "
                              "receipt verification and an admin API. Errors always use the envelope "
                              "`{\"error\": {\"code\", \"message\", \"request_id\"}}`.")
    errors.install(app)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PATCH"],
                           allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Accept-Language"],
                           expose_headers=["X-Request-Id", "Retry-After", "Content-Language"])

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error rid=%s", rid)
            response = JSONResponse(errors.error_body(request, "internal", "internal error"), 500)
        route = request.scope.get("route")
        obs.HTTP_LATENCY.labels(getattr(route, "path", "unmatched"), request.method, response.status_code).observe(
            time.perf_counter() - t0)
        response.headers["x-request-id"] = rid
        if request.url.path.startswith("/v1/"):
            response.headers["Content-Language"] = locale_of(request)
            response.headers.setdefault("Vary", "Accept-Language")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    from payguard.api.routers import ROOT, V1

    for r in V1:
        app.include_router(r, prefix="/v1")
    for r in ROOT:
        app.include_router(r)
    mount_console(app, settings.frontend_dist)
    return app


# The console talks only to this origin; inline styles are allowed for the chart library's SVG.
CONSOLE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
API_PREFIXES = ("v1/", "docs", "redoc", "openapi.json", "healthz", "readyz", "metrics")


def mount_console(app: FastAPI, dist: Path) -> None:
    """Serve the built web console (frontend/dist) from the API's origin: one deployable, no CORS, and the
    API key never leaves the origin. Client-side routes fall back to index.html; unknown API paths still
    get a JSON 404."""
    index = dist / "index.html"
    if not index.exists():
        log.info("web console not built (%s missing); serving the API only", index)
        return
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def console(path: str, request: Request):
        if path.startswith(API_PREFIXES):
            raise errors.ApiError(404, "not_found", "no such endpoint")
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(index, headers={"Content-Security-Policy": CONSOLE_CSP, "Cache-Control": "no-cache",
                                            "X-Frame-Options": "DENY"})


OPENAPI_TAGS = [
    {"name": "auth", "description": "Identity and capabilities of the calling API key."},
    {"name": "scoring", "description": "Synchronous, idempotent decisioning for every payment rail (merchant)."},
    {"name": "transactions", "description": "Scored payments and their decisions (analyst)."},
    {"name": "cases", "description": "Analyst queue ordered by expected loss; resolutions become labels."},
    {"name": "agent", "description": "Investigation agent: queue runs and read grounded reports."},
    {"name": "labels", "description": "Delayed ground truth (chargebacks, analyst outcomes)."},
    {"name": "vision", "description": "Proof-of-payment receipt verification."},
    {"name": "monitoring", "description": "Feature and score drift (PSI)."},
    {"name": "models", "description": "Model registry and hot-swap promotion (admin)."},
    {"name": "admin", "description": "Admin dashboard: analytics, rails, rules, events, API clients, audit, system."},
    {"name": "ops", "description": "Health, readiness and Prometheus metrics."},
]
