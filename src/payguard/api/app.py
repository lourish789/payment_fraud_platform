"""FastAPI application and composition root."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from payguard import observability as obs
from payguard.agent.runner import Provider, make_provider
from payguard.api.security import Authenticator, RedisTokenBucket, TokenBucket
from payguard.config import Settings, get_settings
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


def build_container(settings: Settings, store: FeatureStore | None = None, bus: EventBus | None = None,
                    provider: Provider | None = None, start_workers: bool | None = None) -> Container:
    engine = make_engine(settings.database_url)
    init_db(engine)
    sf = make_session_factory(engine)
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
    explainer = Explainer(sf, models.registry)
    run_workers = settings.run_workers_in_process if start_workers is None else start_workers
    workers = WorkerPool(sf, bus, provider, explainer, settings.agent_auto_investigate) if run_workers else None
    return Container(settings, sf, store, bus, models, rules,
                     ScoringService(FeaturePipeline(store), models, rules, sf),
                     Authenticator(sf), limiter, provider, workers,
                     ReceiptVerifier(settings.artifacts_dir / "vision" / "thresholds.json"), explainer)


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

    app = FastAPI(title="PayGuard", version="0.1.0", lifespan=lifespan,
                  description="Real-time payment fraud scoring, case management, investigation agent and receipt verification.")

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error rid=%s", rid)
            response = JSONResponse({"error": {"code": "internal", "message": "internal error", "request_id": rid}}, 500)
        route = request.scope.get("route")
        obs.HTTP_LATENCY.labels(getattr(route, "path", "unmatched"), request.method, response.status_code).observe(
            time.perf_counter() - t0)
        response.headers["x-request-id"] = rid
        return response

    from payguard.api.routes import router

    app.include_router(router)
    return app
