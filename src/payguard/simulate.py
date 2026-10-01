"""Traffic simulation against the real services: replay, training/serving parity audit, load test, drift demo."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import select

from payguard.data.adapter import iter_transactions
from payguard.data.splits import VALID_END
from payguard.db.models import DecisionRecord, Label

log = logging.getLogger(__name__)


def test_month(processed_dir: Path, n: int | None = None, offset: int = 0) -> pd.DataFrame:
    df = pd.read_parquet(processed_dir / "transactions.parquet", filters=[("event_time", ">=", VALID_END)])
    df = df.sort_values(["TransactionDT", "TransactionID"], kind="stable").reset_index(drop=True)
    return df.iloc[offset: offset + n if n else None]


def replay(container, processed_dir: Path, n: int | None = None, offset: int = 0, mean_label_delay_days: float = 5.0,
           seed: int = 7, corrupt: callable = None) -> dict:
    """Score the test month in event-time order through ScoringService, then record ground truth as
    labels that *arrive late* (exponential delay): tools and monitoring only see a label once it has
    arrived, exactly as with real chargebacks."""
    from payguard.api.security import create_client

    df = test_month(processed_dir, n, offset)
    client_id, _ = create_client(container.session_factory, "replay", "merchant")
    rng = random.Random(seed)
    counts: dict[str, int] = {}
    labels, lat = [], []
    t0 = time.time()
    for i, (txn, y) in enumerate(iter_transactions(df)):
        if corrupt:
            txn = corrupt(txn)
        t = time.perf_counter()
        res = container.scoring.score(txn, client_id)
        lat.append((time.perf_counter() - t) * 1000)
        counts[res.decision.value] = counts.get(res.decision.value, 0) + 1
        labels.append(Label(transaction_id=txn.transaction_id, is_fraud=bool(y), source="chargeback",
                            created_at=txn.event_time + timedelta(days=rng.expovariate(1 / mean_label_delay_days))))
        if (i + 1) % 10_000 == 0:
            log.info("replayed %d (%.0f tx/s)", i + 1, (i + 1) / (time.time() - t0))
    with container.session_factory() as s, s.begin():
        for lab in labels:
            s.merge(lab)
    return {"n": len(df), "decisions": counts, "seconds": round(time.time() - t0, 1),
            "in_process_latency_ms": {"p50": float(np.percentile(lat, 50)), "p95": float(np.percentile(lat, 95)),
                                      "p99": float(np.percentile(lat, 99))}}


def parity_audit(session_factory, processed_dir: Path, sample: int | None = None, tol: float = 1e-4) -> dict:
    """Compare features the API computed at serving time (stored per decision) with the offline backfill
    used for training. Any mismatch is training/serving skew. Streams both sides in batches (bounded memory)."""
    rng = random.Random(0)
    pf = pq.ParquetFile(processed_dir / "features.parquet")
    checked = mismatched_rows = values_compared = 0
    per_feature: dict[str, int] = {}
    for rb in pf.iter_batches(batch_size=10_000):
        batch = rb.to_pandas()
        batch = batch[batch["split"] == "test"]
        if sample:
            batch = batch[[rng.random() < sample / 89_326 for _ in range(len(batch))]]
        if batch.empty:
            continue
        ids = batch["transaction_id"].tolist()
        served: dict[str, dict] = {}
        with session_factory() as s:
            for i in range(0, len(ids), 500):  # SQLite bound-parameter limit
                served.update(s.execute(select(DecisionRecord.transaction_id, DecisionRecord.features)
                                        .where(DecisionRecord.transaction_id.in_(ids[i:i + 500]))).all())
        for rec in batch.to_dict("records"):
            online = served.get(rec["transaction_id"])
            if online is None:  # not replayed
                continue
            bad = False
            for k, v in rec.items():
                if k in ("transaction_id", "event_time", "label", "split"):
                    continue
                o = online.get(k)
                values_compared += 1
                if _missing(v) and _missing(o):
                    continue
                if isinstance(v, str) or isinstance(o, str):
                    same = str(v) == str(o)
                else:
                    # offline is stored as float32; compare at float32 precision
                    same = not _missing(v) and not _missing(o) and math.isclose(float(np.float32(o)), float(v),
                                                                                rel_tol=tol, abs_tol=tol)
                if not same:
                    per_feature[k] = per_feature.get(k, 0) + 1
                    bad = True
            checked += 1
            mismatched_rows += bad
    return {"rows_checked": checked, "values_compared": values_compared, "rows_with_mismatch": mismatched_rows,
            "mismatch_rate": mismatched_rows / max(checked, 1), "features_with_mismatch": per_feature}


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


async def loadtest(url: str, api_key: str, processed_dir: Path, n: int, concurrency: int, offset: int) -> dict:
    import httpx

    df = test_month(processed_dir, n, offset)
    payloads = [t.model_dump(mode="json") for t, _ in iter_transactions(df)]
    lat: list[float] = []
    status: dict[int, int] = {}
    q: asyncio.Queue = asyncio.Queue()
    for p in payloads:
        q.put_nowait(p)
    headers = {"Authorization": f"Bearer {api_key}"}

    async def worker(client):
        while not q.empty():
            p = q.get_nowait()
            t = time.perf_counter()
            r = await client.post(f"{url}/v1/transactions/score", json=p, headers=headers)
            lat.append((time.perf_counter() - t) * 1000)
            status[r.status_code] = status.get(r.status_code, 0) + 1

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        await asyncio.gather(*(worker(client) for _ in range(concurrency)))
    wall = time.perf_counter() - t0
    return {"requests": len(payloads), "concurrency": concurrency, "seconds": round(wall, 2),
            "throughput_rps": round(len(payloads) / wall, 1), "status": status,
            "latency_ms": {k: round(float(np.percentile(lat, q)), 2) for k, q in (("p50", 50), ("p95", 95), ("p99", 99))}}


def drift_demo(settings, out: Path, n: int = 4000) -> dict:
    """Incident drill: an upstream processor starts sending signal C13 as 0 and amounts arrive in minor
    units (x100) for a slice of traffic. Show that the monitor catches it before labels would."""
    from payguard.api.app import build_container

    results = {}
    for name, corrupt in (("baseline", None), ("incident", _corrupt)):
        db = out / f"drift_{name}.db"
        db.unlink(missing_ok=True)
        c = build_container(settings.model_copy(update={"database_url": f"sqlite:///{db.as_posix()}"}),
                            start_workers=False)
        replay(c, settings.processed_dir, n=n, offset=20_000, corrupt=corrupt)
        from payguard.monitoring import drift_report

        rep = drift_report(c.session_factory, c.models.champion)
        results[name] = {k: rep[k] for k in ("status", "score_psi", "flag_rate", "drifted_features")} | {
            "top_psi": dict(list(rep["features_psi"].items())[:5])}
    out.joinpath("drift_demo.json").write_text(json.dumps(results, indent=2))
    return results


def _corrupt(txn):
    rng = random.Random(txn.transaction_id)
    signals = dict(txn.signals)
    if "C13" in signals:
        signals["C13"] = 0.0
    amount = txn.amount * 100 if rng.random() < 0.3 else txn.amount
    return txn.model_copy(update={"signals": signals, "amount": amount})
