"""Crypto data: Elliptic++ (real Bitcoin transactions labelled illicit/licit by Elliptic) and the OFAC SDN
digital-currency address list.

Elliptic++ (Elmougy & Liu, KDD 2023) extends the Elliptic dataset (Weber et al., 2019): 203,769
transactions in 49 time steps (~2 weeks apart, 2016-17), 4,545 illicit / 42,019 licit / the rest
unknown, plus transaction-address edges. We deliberately do NOT use its wallet-level features: they are
lifetime aggregates (identical at every time step, e.g. an address "knows" its last-seen block at its
first time step), which would leak the future into any time-ordered evaluation.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

HF = "https://huggingface.co/datasets/AI4FinTech/ellipticpp/resolve/main"
HF_TREE = "https://huggingface.co/api/datasets/AI4FinTech/ellipticpp/tree/main"
FILES = ["txs_features.csv", "txs_classes.csv", "txs_edgelist.csv", "AddrTx_edgelist.csv", "TxAddr_edgelist.csv"]
OFAC_BASE = "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/lists"
OFAC_ASSETS = ["XBT", "ETH", "USDT", "USDC", "TRX", "LTC", "BCH", "XMR", "SOL", "DASH", "ZEC", "ETC", "BSC", "ARB"]

# Elliptic time steps are ~2 weeks apart; anchored so step 25 matches block ~439,586 (late Nov 2016).
STEP_ANCHOR = datetime(2016, 11, 21, tzinfo=timezone.utc)
STEP_ANCHOR_STEP = 25
STEP_DAYS = 14.0


def step_of(t: datetime) -> int:
    return int(STEP_ANCHOR_STEP + np.floor((t - STEP_ANCHOR).total_seconds() / (STEP_DAYS * 86400)))


def time_of_step(step: int) -> datetime:
    return STEP_ANCHOR + pd.Timedelta(days=STEP_DAYS * (step - STEP_ANCHOR_STEP)).to_pytimedelta()


def _download(url: str, path: Path, size: int | None = None, attempts: int = 40) -> None:
    import httpx

    for i in range(attempts):
        have = path.stat().st_size if path.exists() and size is not None else 0
        if size is not None and have >= size:
            return
        # Resume only when the expected size is known; small lists are simply re-fetched whole.
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as r:
                if r.status_code == 416:
                    return
                r.raise_for_status()
                with path.open("ab" if r.status_code == 206 else "wb") as f:
                    for block in r.iter_bytes(1 << 20):
                        f.write(block)
            if size is None:
                return
        except httpx.HTTPError as e:
            log.warning("download %s attempt %d: %s", path.name, i + 1, e)
            time.sleep(min(2 * (i + 1), 30))
    if size is not None and path.stat().st_size != size:
        raise RuntimeError(f"{path.name}: incomplete download")


def ingest(raw: Path, out: Path) -> dict:
    import httpx

    raw.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    sizes = {f["path"]: (f.get("lfs") or {}).get("size") or f.get("size")
             for f in httpx.get(HF_TREE, timeout=60).json()}
    for name in FILES:
        _download(f"{HF}/{name}", raw / name, sizes.get(name))
    ofac = raw / "ofac"
    ofac.mkdir(exist_ok=True)
    for asset in OFAC_ASSETS:
        _download(f"{OFAC_BASE}/sanctioned_addresses_{asset}.txt", ofac / f"sanctioned_addresses_{asset}.txt")
    (ofac / "fetched_at.txt").write_text(datetime.now(timezone.utc).isoformat())

    header = pd.read_csv(raw / "txs_features.csv", nrows=0).columns
    # txId MUST be read as int64: float32 is exact only up to 2^24 and Elliptic ids reach ~2.3e8, so a
    # float32 read silently rounds ids and breaks the label join (caught: 3/4 of labels went missing).
    feats = pd.read_csv(raw / "txs_features.csv",
                        dtype={c: (np.int64 if c == "txId" else np.float32) for c in header})
    feats = feats.rename(columns={"Time step": "step"})
    feats["step"] = feats["step"].astype(np.int16)
    classes = pd.read_csv(raw / "txs_classes.csv", dtype={"txId": np.int64, "class": np.int8})
    # Elliptic coding: 1 illicit, 2 licit, 3 unknown -> label 1 / 0 / -1
    classes["label"] = classes["class"].map({1: 1, 2: 0, 3: -1}).astype(np.int8)
    df = feats.merge(classes[["txId", "label"]], on="txId", how="left", validate="one_to_one")
    if df["label"].isna().any():
        raise RuntimeError(f"{int(df['label'].isna().sum())} transactions without a class row")
    df.to_parquet(out / "crypto_txs.parquet", index=False)
    manifest = {"rows": int(len(df)), "steps": [int(df.step.min()), int(df.step.max())],
                "labels": {str(k): int(v) for k, v in df.label.value_counts().items()},
                "n_features": int(df.shape[1] - 3), "ofac_fetched_at": (ofac / "fetched_at.txt").read_text()}
    (out / "crypto_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
