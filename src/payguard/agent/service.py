"""Persisting investigations: queued -> running -> done|failed, with trace and token usage."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from payguard import observability as obs
from payguard.agent.runner import Provider
from payguard.agent.tools import InvestigationTools
from payguard.db.models import Investigation, clean_json
from payguard.db.session import write_guard
from payguard.events import enqueue

log = logging.getLogger(__name__)


def enqueue_investigation(session_factory: sessionmaker, case_id: str, requested_by: str | None = None) -> str:
    """Idempotent per case: an unfinished investigation is reused rather than duplicated.

    An analyst's request (requested_by set) also emits `investigation.requested` through the outbox in
    the same transaction, so a worker in another process runs it on its interactive lane."""
    with write_guard(session_factory), session_factory() as s, s.begin():
        inv_id = s.scalar(select(Investigation.id).where(Investigation.case_id == case_id,
                                                          Investigation.status.in_(("queued", "running"))))
        if inv_id is None:
            inv = Investigation(case_id=case_id)
            s.add(inv)
            s.flush()
            inv_id = inv.id
        if requested_by:
            enqueue(s, "investigation.requested", inv_id,
                    {"investigation_id": inv_id, "case_id": case_id, "requested_by": requested_by})
        return inv_id


def run_investigation(session_factory: sessionmaker, provider: Provider, inv_id: str, explainer=None) -> Investigation:
    with write_guard(session_factory), session_factory() as s, s.begin():
        inv = s.get(Investigation, inv_id)
        # Claim only a queued investigation: the same id can be submitted twice (auto-investigation on
        # case creation, then an analyst's request for the same case), and must run once.
        if inv is None or inv.status != "queued":
            return inv
        inv.status, inv.provider = "running", provider.name
        case_id = inv.case_id
    tools = InvestigationTools(session_factory, explainer)
    try:
        result = provider.investigate(tools, tools.context(case_id))
        error = result.error
    except Exception as e:  # a crashing agent must not take the worker down
        log.exception("investigation %s crashed", inv_id)
        result, error = None, f"{type(e).__name__}: {e}"
    with write_guard(session_factory), session_factory() as s, s.begin():
        inv = s.get(Investigation, inv_id)
        inv.finished_at = datetime.now(timezone.utc)
        if result is not None:
            inv.model, inv.trace = result.model, clean_json(result.trace)
            inv.input_tokens, inv.output_tokens = result.input_tokens, result.output_tokens
            obs.AGENT_TOKENS.labels("input").inc(result.input_tokens)
            obs.AGENT_TOKENS.labels("output").inc(result.output_tokens)
        if result is not None and result.report is not None:
            inv.status = "done"
            inv.recommendation, inv.confidence = result.report.recommendation, result.report.confidence
            inv.report = {**result.report.model_dump(), "grounding": result.grounding}
        else:
            inv.status, inv.error = "failed", error
        obs.AGENT_RUNS.labels(provider.name, inv.status).inc()
        s.flush()
        return inv
