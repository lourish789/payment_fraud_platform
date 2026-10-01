"""Drift monitoring: Population Stability Index of live serving-time features and scores against the
reference distributions stored with the model (validation month).

PSI < 0.1 stable | 0.1-0.25 investigate | > 0.25 significant shift (conventional credit-risk bands).
Labels arrive weeks late, so input and score drift are the earliest signals that a model is decaying;
live precision from analyst labels is reported alongside when enough labels exist.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from payguard.db.models import DecisionRecord, Label, Transaction
from payguard.models.registry import ModelBundle

WARN, ALERT = 0.10, 0.25


def psi(expected: list[float], actual: list[float], eps: float = 1e-4) -> float:
    e = np.clip(np.asarray(expected), eps, None)
    a = np.clip(np.asarray(actual), eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


def _bin_props(x: np.ndarray, edges: list[float]) -> list[float]:
    edges = np.asarray(edges)
    missing = np.isnan(x)
    idx = np.clip(np.searchsorted(edges, x[~missing], side="right") - 1, 0, len(edges) - 2)
    counts = np.append(np.bincount(idx, minlength=len(edges) - 1).astype(float), missing.sum())
    return (counts / max(len(x), 1)).tolist()


def drift_report(session_factory: sessionmaker, model: ModelBundle, since: datetime | None = None,
                 limit: int = 20_000, top_features: int = 25) -> dict:
    with session_factory() as s:
        q = (select(DecisionRecord.features, DecisionRecord.fraud_probability, DecisionRecord.decision,
                    Transaction.event_time, DecisionRecord.transaction_id)
             .join(Transaction, Transaction.id == DecisionRecord.transaction_id)
             .where(DecisionRecord.model_version == model.version)
             .order_by(DecisionRecord.id.desc()).limit(limit))
        if since is not None:
            q = q.where(Transaction.event_time >= since)
        rows = s.execute(q).all()
        flagged_ids = [r[4] for r in rows if r[2] != "approve"]
        labels = dict(s.execute(select(Label.transaction_id, Label.is_fraud)
                                .where(Label.transaction_id.in_(flagged_ids))).all()) if flagged_ids else {}
    if len(rows) < 500:
        return {"status": "insufficient_data", "n": len(rows)}

    ref = model.reference
    important = [f for f, _ in model.metadata["report"]["top_features"][:top_features]]
    features = {}
    for f in important:
        if f in ref["numeric"]:
            x = np.array([_to_float(r[0].get(f)) for r in rows])
            features[f] = psi(ref["numeric"][f]["props"], _bin_props(x, ref["numeric"][f]["edges"]))
        elif f in ref["categorical"]:
            cats = ref["categorical"][f]
            vals = [r[0].get(f) if r[0].get(f) is not None else "<missing>" for r in rows]
            n = len(vals)
            actual = [sum(v == c for v in vals) / n for c in cats]
            expected = list(cats.values())
            actual.append(max(1 - sum(actual), 0))
            expected.append(max(1 - sum(expected), 0))
            features[f] = psi(expected, actual)
    scores = np.array([r[1] for r in rows])
    score_psi = psi(ref["score"]["props"], _bin_props(scores, ref["score"]["edges"]))
    flagged = [r for r in rows if r[2] != "approve"]
    labelled = [labels[r[4]] for r in flagged if r[4] in labels]
    worst = sorted(features.items(), key=lambda kv: -kv[1])
    status = "alert" if score_psi > ALERT or any(v > ALERT for v in features.values()) else (
        "warn" if score_psi > WARN or any(v > WARN for v in features.values()) else "ok")
    times = [r[3] for r in rows]
    return {
        "status": status, "n": len(rows), "model_version": model.version,
        "window": {"from": str(min(times)), "to": str(max(times))},
        "score_psi": round(score_psi, 4),
        "flag_rate": round(len(flagged) / len(rows), 4),
        "flagged_labelled": len(labelled),
        "live_precision_flagged": round(float(np.mean(labelled)), 4) if len(labelled) >= 30 else None,
        "features_psi": {k: round(v, 4) for k, v in worst},
        "drifted_features": [k for k, v in worst if v > WARN],
        "thresholds": {"warn": WARN, "alert": ALERT},
    }


def _to_float(v) -> float:
    try:
        return float(v) if v is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")
