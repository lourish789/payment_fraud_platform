"""Offline backfill: replay the event log, in event-time order, through the exact online feature code.

Each transaction's streaming features are read from state *before* it is folded in, so every training
row is point-in-time correct (no future leakage). At the start of the test month the entity states
are materialised to a snapshot; loading that snapshot into the online store and replaying the test
month through the API must reproduce these features exactly (see tests/test_parity.py and
`payguard parity`).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from payguard.config import get_settings
from payguard.data.adapter import iter_transactions
from payguard.data.splits import VALID_END, split_of
from payguard.features.pipeline import CATEGORICAL, FeaturePipeline
from payguard.features.store import InMemoryFeatureStore, save_snapshot

log = logging.getLogger(__name__)
META = ["transaction_id", "event_time", "label", "split"]


def _schema(feature_names: list[str]) -> pa.Schema:
    fields = [pa.field("transaction_id", pa.string()), pa.field("event_time", pa.timestamp("s", tz="UTC")),
              pa.field("label", pa.int8()), pa.field("split", pa.string())]
    for n in feature_names:
        fields.append(pa.field(n, pa.string() if n in CATEGORICAL else pa.float32()))
    return pa.schema(fields)


def _to_table(rows: list[dict], schema: pa.Schema) -> pa.Table:
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema}
    return pa.Table.from_pydict(cols, schema=schema)


def backfill(chunk: int = 20_000) -> Path:
    s = get_settings()
    df = pd.read_parquet(s.processed_dir / "transactions.parquet")
    df["split"] = split_of(df["event_time"])
    splits = df["split"].to_numpy()
    out = s.processed_dir / "features.parquet"
    snap = s.processed_dir / "online_snapshot.jsonl.gz"

    store = InMemoryFeatureStore()
    pipe = FeaturePipeline(store)
    writer: pq.ParquetWriter | None = None
    schema = None
    rows: list[dict] = []
    snapshot_taken = False
    t0 = time.time()
    for i, (txn, label) in enumerate(iter_transactions(df)):
        if not snapshot_taken and txn.event_time >= VALID_END.to_pydatetime():
            save_snapshot(store, snap, as_of=str(VALID_END))
            snapshot_taken = True
            log.info("snapshot at %s: %d entities", VALID_END, len(store))
        feats, _ = pipe.build(txn, dedupe=False)
        feats.update(transaction_id=txn.transaction_id, event_time=txn.event_time, label=label, split=splits[i])
        rows.append(feats)
        if len(rows) >= chunk:
            if schema is None:
                schema = _schema([k for k in rows[0] if k not in META])
                writer = pq.ParquetWriter(out, schema, compression="zstd")
            writer.write_table(_to_table(rows, schema))
            rows = []
            if (i + 1) % 100_000 < chunk:
                log.info("%d rows (%.0f rows/s)", i + 1, (i + 1) / (time.time() - t0))
    if rows:
        if schema is None:
            schema = _schema([k for k in rows[0] if k not in META])
            writer = pq.ParquetWriter(out, schema, compression="zstd")
        writer.write_table(_to_table(rows, schema))
    writer.close()
    log.info("backfill done: %s in %.0fs", out, time.time() - t0)
    return out
