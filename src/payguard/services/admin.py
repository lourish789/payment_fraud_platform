"""Aggregates behind the admin dashboard: one place that reflects the whole system (traffic and decisions
per rail, the case queue, labels and live precision, the agent, receipts, the event pipeline, models,
rules and component health).

Time windows are on business time (`event_time`) and end at the wall clock by default. `anchor=latest_event`
ends them at the newest event instead, which is useful for inspecting a replayed historical month (the
IEEE-CIS test month is May 2018) in isolation, but it is not the default: as soon as live traffic is
mixed in, "latest event" means today and the replay drops out of view. Every response says which anchor
it used.

Aggregations run in SQL (GROUP BY) except percentiles and rule-hit counts, which use a bounded sample of
the most recent rows so the dashboard costs the same at 10k or 10M transactions.
"""

from __future__ import annotations

import statistics
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import case as sql_case
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from payguard import __version__
from payguard.db.models import Case, DecisionRecord, Investigation, Label, OutboxEvent, Receipt, Transaction
from payguard.schemas import RAILS

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30),
           "90d": timedelta(days=90), "all": None}
SAMPLE = 20_000
STARTED_AT = datetime.now(timezone.utc)


def _aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def resolve_window(sf: sessionmaker, window: str, anchor: str, start: datetime | None = None,
                   end: datetime | None = None) -> dict:
    if start or end:
        return {"from": _aware(start), "to": _aware(end), "anchored_to": "explicit"}
    span = WINDOWS[window]
    if anchor == "now":
        to = datetime.now(timezone.utc)
    else:
        with sf() as s:
            to = _aware(s.scalar(select(func.max(Transaction.event_time))))
        if to is None:
            to = datetime.now(timezone.utc)
        to = to + timedelta(microseconds=1)  # make the latest event fall inside [from, to)
    return {"from": None if span is None else to - span, "to": to, "anchored_to": "now" if anchor == "now" else "latest_event"}


def _in_window(stmt, w: dict, col=Transaction.event_time):
    if w["from"] is not None:
        stmt = stmt.where(col >= w["from"])
    if w["to"] is not None:
        stmt = stmt.where(col < w["to"])
    return stmt


def _pct(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 3) if values else None


def overview(container, w: dict) -> dict:
    sf = container.session_factory
    with sf() as s:
        # One grouped scan (index-only, see ix_transactions_dashboard) gives traffic, decisions, rails,
        # labels and the labelled confusion matrix.
        core = s.execute(_in_window(
            select(Transaction.rail, DecisionRecord.decision, Label.is_fraud, Label.source, func.count(),
                   func.sum(Transaction.amount), func.sum(DecisionRecord.fraud_probability))
            .select_from(Transaction)
            .join(DecisionRecord, DecisionRecord.transaction_id == Transaction.id)
            .outerjoin(Label, Label.transaction_id == Transaction.id)
            .group_by(Transaction.rail, DecisionRecord.decision, Label.is_fraud, Label.source), w)).all()
        latencies = [r for (r,) in s.execute(_in_window(
            select(DecisionRecord.latency_ms).join(Transaction, Transaction.id == DecisionRecord.transaction_id)
            .order_by(Transaction.event_time.desc()).limit(SAMPLE), w)).all()]

        case_rows = s.execute(_in_window(
            select(Case.status, Case.resolution, func.count(), func.sum(Case.priority), func.min(Case.created_at))
            .join(Transaction, Transaction.id == Case.transaction_id)
            .group_by(Case.status, Case.resolution), w)).all()
        resolve_times = [((_aware(b) - _aware(a)).total_seconds()) for a, b in s.execute(_in_window(
            select(Case.created_at, Case.resolved_at).join(Transaction, Transaction.id == Case.transaction_id)
            .where(Case.resolved_at.is_not(None)).order_by(Case.resolved_at.desc()).limit(2000), w)).all()]

        inv_rows = s.execute(_in_window(
            select(Investigation.status, Investigation.recommendation, Case.resolution, func.count(),
                   func.sum(Investigation.input_tokens), func.sum(Investigation.output_tokens))
            .select_from(Investigation).join(Case, Case.id == Investigation.case_id)
            .join(Transaction, Transaction.id == Case.transaction_id)
            .group_by(Investigation.status, Investigation.recommendation, Case.resolution), w)).all()

        receipts = dict(s.execute(select(Receipt.verdict, func.count()).group_by(Receipt.verdict)).all())
        outbox_pending, outbox_oldest = s.execute(
            select(func.count(), func.min(OutboxEvent.created_at)).where(OutboxEvent.published_at.is_(None))).one()
        outbox_total = s.scalar(select(func.count()).select_from(OutboxEvent))

    by_decision = {"approve": 0, "review": 0, "decline": 0}
    by_rail: dict[str, dict] = {}
    labels: dict = {"total": 0, "fraud": 0, "by_source": {}}
    tp = fp = fn = 0
    n = flagged_n = 0
    amount = flagged_amount = sum_p = 0.0
    for rail, decision, is_fraud, source, cnt, amt, p_sum in core:
        rail, amt, is_flagged = rail or "card", float(amt or 0), decision != "approve"
        n, amount, sum_p = n + cnt, amount + amt, sum_p + float(p_sum or 0)
        if is_flagged:
            flagged_n, flagged_amount = flagged_n + cnt, flagged_amount + amt
        by_decision[decision] = by_decision.get(decision, 0) + cnt
        r = by_rail.setdefault(rail, {"rail": rail, "total": 0, "amount": 0.0, "approve": 0, "review": 0, "decline": 0})
        r["total"] += cnt
        r["amount"] += amt
        r[decision] = r.get(decision, 0) + cnt
        if is_fraud is not None:
            labels["total"] += cnt
            labels["fraud"] += cnt if is_fraud else 0
            labels["by_source"][source] = labels["by_source"].get(source, 0) + cnt
            tp += cnt if is_flagged and is_fraud else 0
            fp += cnt if is_flagged and not is_fraud else 0
            fn += cnt if not is_flagged and is_fraud else 0
    for r in by_rail.values():
        r["flag_rate"] = round((r["review"] + r["decline"]) / r["total"], 4) if r["total"] else None
        r["amount"] = round(r["amount"], 2)
    labels["precision_flagged"] = round(tp / (tp + fp), 4) if tp + fp else None
    labels["recall"] = round(tp / (tp + fn), 4) if tp + fn else None
    labels["fraud_rate"] = round(labels["fraud"] / labels["total"], 4) if labels["total"] else None
    mean_p = sum_p / n if n else None

    cases = {"open": 0, "resolved": 0, "resolved_fraud": 0, "resolved_legit": 0, "open_exposure": 0.0}
    oldest_open = None
    for status, resolution, cnt, exposure, first in case_rows:
        cases[status] = cases.get(status, 0) + cnt
        if status == "resolved" and resolution in ("fraud", "legit"):
            cases[f"resolved_{resolution}"] += cnt
        if status == "open":
            cases["open_exposure"] += float(exposure or 0)
            oldest_open = min(filter(None, (oldest_open, _aware(first))), default=None)

    inv_status: dict[str, int] = {}
    inv_rec: dict[str, int] = {}
    tokens_in = tokens_out = 0
    agree: dict[bool, int] = {}
    for status, rec, resolution, cnt, t_in, t_out in inv_rows:
        inv_status[status] = inv_status.get(status, 0) + cnt
        tokens_in, tokens_out = tokens_in + int(t_in or 0), tokens_out + int(t_out or 0)
        if status == "done":
            inv_rec[str(rec)] = inv_rec.get(str(rec), 0) + cnt
            if rec in ("fraud", "legit") and resolution in ("fraud", "legit"):
                agree[rec == resolution] = agree.get(rec == resolution, 0) + cnt
    inv_tokens = (tokens_in, tokens_out)

    agree_d = agree
    compared = sum(agree_d.values())
    now = datetime.now(timezone.utc)
    champion, challenger = container.models.current()
    card_metrics = (champion.metadata.get("report", {}).get("test", {}).get("calibrated", {}) if champion else {})

    return {
        "window": w,
        "transactions": {"total": int(n), "amount": round(float(amount), 2), "flagged": int(flagged_n),
                         "flagged_amount": round(float(flagged_amount), 2),
                         "flag_rate": round(flagged_n / n, 4) if n else None,
                         "mean_fraud_probability": None if mean_p is None else round(float(mean_p), 5)},
        "by_decision": by_decision,
        "by_rail": sorted(by_rail.values(), key=lambda r: RAILS.index(r["rail"]) if r["rail"] in RAILS else 99),
        "latency_ms": {"p50": _pct(latencies, 50), "p95": _pct(latencies, 95), "p99": _pct(latencies, 99),
                       "sampled": len(latencies)},
        "cases": {**cases, "open_exposure": round(cases["open_exposure"], 2),
                  "oldest_open_age_s": None if oldest_open is None else round((now - oldest_open).total_seconds()),
                  "median_time_to_resolve_s": round(statistics.median(resolve_times)) if resolve_times else None},
        "labels": labels,
        "agent": {"provider": container.provider.name, "by_status": inv_status,
                  "by_recommendation": inv_rec,
                  "tokens": {"input": int(inv_tokens[0]), "output": int(inv_tokens[1])},
                  "analyst_agreement": round(agree_d.get(True, 0) / compared, 4) if compared else None,
                  "compared": compared},
        "receipts": receipts,
        "events": {"total": int(outbox_total or 0), "pending": int(outbox_pending or 0),
                   "oldest_pending_age_s": None if outbox_oldest is None
                   else round((now - _aware(outbox_oldest)).total_seconds(), 1)},
        "models": {"champion": champion.version if champion else None,
                   "challenger": challenger.version if challenger else None,
                   "card_test_metrics": {k: round(v, 4) for k, v in card_metrics.items() if isinstance(v, float)}},
        "health": health(container),
    }


def health(container) -> dict[str, bool]:
    checks = {"model": container.models.champion is not None}
    try:
        checks["feature_store"] = bool(container.store.ping())
    except Exception:
        checks["feature_store"] = False
    try:
        with container.session_factory() as s:
            s.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    checks["workers"] = container.workers is not None or not container.settings.run_workers_in_process
    return checks


def _bucket(dialect: str, col, bucket: str):
    if dialect == "sqlite":
        return func.strftime("%Y-%m-%dT%H:00:00" if bucket == "hour" else "%Y-%m-%d", col)
    fmt = 'YYYY-MM-DD"T"HH24:00:00' if bucket == "hour" else "YYYY-MM-DD"
    return func.to_char(func.date_trunc(bucket, col), fmt)


def timeseries(sf: sessionmaker, w: dict, bucket: str) -> dict:
    with sf() as s:
        b = _bucket(s.bind.dialect.name, Transaction.event_time, bucket).label("b")
        rows = s.execute(_in_window(
            select(b, Transaction.rail, DecisionRecord.decision, func.count(), func.sum(Transaction.amount))
            .join(DecisionRecord, DecisionRecord.transaction_id == Transaction.id)
            .group_by(b, Transaction.rail, DecisionRecord.decision).order_by(b), w)).all()
    series: dict[str, dict] = {}
    by_rail: dict[str, dict[str, dict]] = {}
    for t, rail, decision, cnt, amt in rows:
        p = series.setdefault(t, {"t": t, "total": 0, "approve": 0, "review": 0, "decline": 0,
                                  "amount": 0.0, "flagged_amount": 0.0})
        p["total"] += cnt
        p[decision] += cnt
        p["amount"] += float(amt or 0)
        if decision != "approve":
            p["flagged_amount"] += float(amt or 0)
        r = by_rail.setdefault(rail or "card", {}).setdefault(t, {"t": t, "total": 0, "flagged": 0})
        r["total"] += cnt
        r["flagged"] += cnt if decision != "approve" else 0
    for p in series.values():
        p["amount"], p["flagged_amount"] = round(p["amount"], 2), round(p["flagged_amount"], 2)
    return {"window": w, "bucket": bucket, "series": list(series.values()),
            "by_rail": {k: list(v.values()) for k, v in by_rail.items()}}


# ---- rails & rules ------------------------------------------------------------------------------------
def _cond(c) -> dict:
    return {"feature": c.feature, "op": c.op, "value": c.value}


def _rule_hits(sf: sessionmaker) -> tuple[Counter, int]:
    hits: Counter = Counter()
    with sf() as s:
        rows = s.execute(select(Transaction.rail, DecisionRecord.rules)
                         .join(Transaction, Transaction.id == DecisionRecord.transaction_id)
                         .order_by(DecisionRecord.id.desc()).limit(SAMPLE)).all()
    for rail, rules in rows:
        for r in rules or []:
            hits[(rail or "card", r.removeprefix("shadow:"))] += 1
    return hits, len(rows)


def rules_report(container) -> dict:
    hits, n = _rule_hits(container.session_factory)
    out = []
    engines = [("card", container.rules)] if "card" in container.scoring.scorers else []
    engines += [(rail, sc.rules) for rail, sc in container.scoring.scorers.items() if rail != "card"]
    for rail, engine in engines:
        for r in engine.rules:
            out.append({"rail": rail, "id": r.id, "description": r.description, "action": r.action.value,
                        "mode": r.mode, "conditions": [_cond(c) for c in r.conditions], "hits": hits[(rail, r.id)]})
    return {"sampled_decisions": n, "rules": out}


def rails_report(container) -> list[dict]:
    scorers = container.scoring.scorers
    with container.session_factory() as s:
        rows = s.execute(select(Transaction.rail, DecisionRecord.decision, func.count(), func.avg(DecisionRecord.latency_ms))
                         .join(DecisionRecord, DecisionRecord.transaction_id == Transaction.id)
                         .group_by(Transaction.rail, DecisionRecord.decision)).all()
    stats: dict[str, dict] = {}
    for rail, decision, cnt, lat in rows:
        st = stats.setdefault(rail or "card", {"total": 0, "approve": 0, "review": 0, "decline": 0, "_lat": 0.0})
        st["total"] += cnt
        st[decision] += cnt
        st["_lat"] += float(lat or 0) * cnt
    out = []
    for rail in RAILS:
        sc = scorers.get(rail)
        st = stats.get(rail, {"total": 0, "approve": 0, "review": 0, "decline": 0, "_lat": 0.0})
        st["mean_latency_ms"] = round(st.pop("_lat") / st["total"], 3) if st["total"] else None
        st["flag_rate"] = round((st["review"] + st["decline"]) / st["total"], 4) if st["total"] else None
        if rail == "card":
            champ = container.models.champion
            details = {} if champ is None else {
                "policy": champ.metadata.get("report", {}).get("policy"),
                "train_window": champ.metadata.get("train_window"), "test_window": champ.metadata.get("test_window"),
                "n_features": champ.metadata.get("n_features"),
                "challenger": container.models.challenger.version if container.models.challenger else None}
            out.append({"rail": rail, "enabled": sc is not None, "engine": "model",
                        "version": champ.version if champ else None,
                        "rules": len(container.rules.rules), "details": details, "stats": st})
            continue
        if sc is None:
            out.append({"rail": rail, "enabled": False, "engine": "scorecard", "version": None, "rules": 0,
                        "details": {}, "stats": st})
            continue
        details = {"intercept": sc.card.intercept,
                   "thresholds": {"review": sc.card.review, "decline": sc.card.decline},
                   "weights": [{**_cond(it.cond), "points": it.points, "reason": it.reason} for it in sc.card.items],
                   "travel_rule_threshold_usd": sc.travel_rule_threshold_usd}
        if rail == "crypto":
            details |= {"sanctioned_addresses": len(sc.screener), "intel_version": sc.intel_version,
                        "counterparty_thresholds": None if sc.combiner is None else
                        {"review": sc.combiner.p.get("review_threshold"), "block": sc.combiner.p.get("block_threshold")}}
        out.append({"rail": rail, "enabled": True, "engine": "scorecard",
                    "version": getattr(sc, "version", None) if rail == "crypto" else sc.card.version,
                    "rules": len(sc.rules.rules), "details": details, "stats": st})
    return out


def events_topics(sf: sessionmaker) -> list[dict]:
    now = datetime.now(timezone.utc)
    pending = OutboxEvent.published_at.is_(None)
    with sf() as s:
        rows = s.execute(select(OutboxEvent.topic, func.count(), func.sum(sql_case((pending, 1), else_=0)),
                                func.min(sql_case((pending, OutboxEvent.created_at), else_=None)),
                                func.max(OutboxEvent.created_at))
                         .group_by(OutboxEvent.topic).order_by(OutboxEvent.topic)).all()
    out = []
    for topic, total, n_pending, oldest_pending, last in rows:
        oldest_pending = _aware(oldest_pending) if isinstance(oldest_pending, datetime) else (
            _aware(datetime.fromisoformat(oldest_pending)) if oldest_pending else None)
        out.append({"topic": topic, "total": int(total), "pending": int(n_pending or 0),
                    "oldest_pending_age_s": None if oldest_pending is None
                    else round((now - oldest_pending).total_seconds(), 1),
                    "last_event_at": _aware(last) if isinstance(last, datetime) else last})
    return out


def system_info(container) -> dict:
    st = container.settings
    sf = container.session_factory
    db_url = sf.kw["bind"].url
    return {
        "version": __version__, "started_at": STARTED_AT,
        "uptime_s": round(time.time() - STARTED_AT.timestamp(), 1),
        "components": {
            "database": {"dialect": sf.kw["bind"].dialect.name, "url": db_url.render_as_string(hide_password=True)},
            "feature_store": {"type": type(container.store).__name__},
            "event_bus": {"type": type(container.bus).__name__},
            "rate_limiter": {"type": type(container.limiter).__name__},
            "workers": {"in_process": container.workers is not None},
            "agent": {"provider": container.provider.name, "model": st.agent_model,
                      "auto_investigate": st.agent_auto_investigate},
            "rails": sorted(container.scoring.scorers),
            "health": health(container),
        },
        "settings": {"rails_enabled": st.rails_enabled, "rate_limit_rps": st.rate_limit_rps,
                     "rate_limit_burst": st.rate_limit_burst, "travel_rule_threshold_usd": st.travel_rule_threshold_usd,
                     "agent_max_steps": st.agent_max_steps, "run_workers_in_process": st.run_workers_in_process,
                     "cors_origins": st.cors_origins, "redis": bool(st.redis_url),
                     "default_locale": st.default_locale, "fx": container.fx.public()},
    }
