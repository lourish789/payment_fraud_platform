"""Background workers: outbox relay and the investigation consumer.

Dev mode runs them as threads inside the API process; in docker-compose the same code runs as a
separate `payguard worker` process (scaled independently of the API, and an LLM outage never
touches scoring latency).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from payguard import observability as obs
from payguard.agent.runner import Provider
from payguard.agent.service import enqueue_investigation, run_investigation
from payguard.db.models import OutboxEvent
from payguard.events import EventBus, relay_outbox
from payguard.services.explanations import Explainer

log = logging.getLogger(__name__)


class WorkerPool:
    def __init__(self, session_factory: sessionmaker, bus: EventBus, provider: Provider, explainer: Explainer,
                 auto_investigate: bool = True, agent_concurrency: int = 2, consumer_name: str = "worker-1"):
        self.sf, self.bus, self.provider, self.explainer = session_factory, bus, provider, explainer
        self.auto_investigate = auto_investigate
        self.consumer = consumer_name
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.executor = ThreadPoolExecutor(max_workers=agent_concurrency, thread_name_prefix="agent")

    def submit_investigation(self, inv_id: str) -> None:
        self.executor.submit(run_investigation, self.sf, self.provider, inv_id, self.explainer)

    def _relay_loop(self):
        while not self._stop.is_set():
            try:
                n = relay_outbox(self.sf, self.bus)
                with self.sf() as s:
                    obs.OUTBOX_LAG.set(s.scalar(select(func.count()).select_from(OutboxEvent)
                                                .where(OutboxEvent.published_at.is_(None))))
                if n == 0:
                    self._stop.wait(0.2)
            except Exception:
                log.exception("outbox relay failed; retrying")
                self._stop.wait(1.0)

    def _on_case_created(self, key: str, payload: dict) -> None:
        self.explainer.explain(payload["transaction_id"])  # cached; ready before an analyst or the agent looks
        if not self.auto_investigate:
            return
        inv_id = enqueue_investigation(self.sf, payload["case_id"])  # idempotent per case
        self.submit_investigation(inv_id)

    def _consume_loop(self):
        while not self._stop.is_set():
            try:
                self.bus.poll("case.created", "investigator", self.consumer, self._on_case_created, block_ms=500)
            except Exception:
                log.exception("case consumer failed; retrying")
                self._stop.wait(1.0)

    def start(self) -> None:
        for target in (self._relay_loop, self._consume_loop):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self.executor.shutdown(wait=False, cancel_futures=True)
