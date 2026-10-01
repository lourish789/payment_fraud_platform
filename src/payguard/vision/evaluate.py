"""Evaluate the receipt verifier on generated receipts.

1. Calibrate the forensic ELA band on a *validation* set of genuine receipts (target ~2% false flags).
2. On a separate test set, report verdicts per forgery kind in two regimes:
     with_ledger     the transfer can be looked up in our records (the normal case)
     forensics_only  ledger unavailable (e.g. inbound transfer from another bank) - only ELA can help
"""

from __future__ import annotations

import io
import json
import logging
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np

from payguard.vision.receipts import KINDS, make_case, synthetic_ledger
from payguard.vision.verify import ReceiptVerifier, _Ocr, extract_fields, forensics

log = logging.getLogger(__name__)


def _png(img) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def calibrate(n_genuine: int, seed: int, target_fpr: float = 0.02) -> dict:
    rng = random.Random(seed)
    ledger = synthetic_ledger(rng, 200)
    ratios = []
    for _ in range(n_genuine):
        img, _ = make_case("genuine", ledger, rng)
        f = forensics(img, extract_fields(_Ocr.run(img)), {"ela_low": 0.0, "ela_high": 1e9})
        ratios += [v["ela_edge_ratio"] for v in f.values()]
    r = np.array(ratios)
    tail = target_fpr / 4  # two fields x two tails share the false-flag budget
    return {"ela_low": float(np.quantile(r, tail) * 0.97), "ela_high": float(np.quantile(r, 1 - tail) * 1.03),
            "calibration_n_ratios": len(r)}


def evaluate(out_dir: Path, n_val_genuine: int = 40, n_test_per_kind: int = 30, seed: int = 2024) -> dict:
    t0 = time.time()
    thresholds = calibrate(n_val_genuine, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "thresholds.json").write_text(json.dumps(thresholds, indent=2))
    verifier = ReceiptVerifier(out_dir / "thresholds.json")

    rng = random.Random(seed + 1)
    ledger = synthetic_ledger(rng, 300)
    lookup = ledger.get
    results = {"with_ledger": {}, "forensics_only": {}}
    latencies = []
    for kind in KINDS:
        wl, fo = Counter(), Counter()
        for _ in range(n_test_per_kind):
            img, claimed = make_case(kind, ledger, rng)
            data = _png(img)
            t = time.perf_counter()
            wl[verifier.verify(data, lookup, claimed)["verdict"]] += 1
            latencies.append(time.perf_counter() - t)
            if kind in ("genuine", "amount_edit", "date_edit"):
                fo[verifier.verify(data, None, None)["verdict"]] += 1
        results["with_ledger"][kind] = dict(wl)
        if fo:
            results["forensics_only"][kind] = dict(fo)
        log.info("%s: with_ledger=%s forensics_only=%s", kind, dict(wl), dict(fo))

    def rate(counter: dict, good: set[str]) -> float:
        return sum(v for k, v in counter.items() if k in good) / max(sum(counter.values()), 1)

    bad = {"mismatch", "not_found", "suspected_tampering"}
    summary = {
        "with_ledger": {
            "genuine_verified_rate": rate(results["with_ledger"]["genuine"], {"verified"}),
            **{f"{k}_caught_rate": rate(results["with_ledger"][k], bad) for k in KINDS if k != "genuine"},
        },
        "forensics_only": {
            "genuine_false_flag_rate": rate(results["forensics_only"]["genuine"], {"suspected_tampering"}),
            "amount_edit_caught_rate": rate(results["forensics_only"]["amount_edit"], {"suspected_tampering"}),
            "date_edit_caught_rate": rate(results["forensics_only"]["date_edit"], {"suspected_tampering"}),
        },
        "latency_s": {"p50": float(np.percentile(latencies, 50)), "p95": float(np.percentile(latencies, 95))},
    }
    report = {"thresholds": thresholds, "counts": results, "summary": summary, "n_test_per_kind": n_test_per_kind,
              "seconds": round(time.time() - t0)}
    (out_dir / "vision_eval.json").write_text(json.dumps(report, indent=2))
    return report
