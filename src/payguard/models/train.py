"""Train, calibrate, set policy, evaluate and register a model version.

Protocol (all out-of-time):
  warmup Dec       streaming state warm-up only (history is left-censored), not trained on
  train  Jan-Mar   fit; negatives downsampled to NEG_FRAC (positives kept) to fit the dev box's RAM
  valid  April     early stopping, isotonic calibration (undoes the downsampling bias), policy fit,
                   drift reference distributions
  test   May       touched once, for the numbers in the report

Also trained for the report: ablations (what each feature family buys) and a rules-only baseline.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score, roc_curve

from payguard.config import get_settings
from payguard.features.pipeline import CATEGORICAL, STREAMING_FEATURES
from payguard.models.encoder import FeatureEncoder
from payguard.models.policy import fit_policy, policy_outcomes
from payguard.models.registry import Registry
from payguard.rules import RuleEngine
from payguard.schemas import Decision

log = logging.getLogger(__name__)

META = ["transaction_id", "event_time", "label", "split"]
NEG_FRAC = 0.30
SEED = 7
PARAMS = {
    "objective": "binary", "learning_rate": 0.03, "num_leaves": 63, "min_data_in_leaf": 100,
    "feature_fraction": 0.5, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
    "cat_smooth": 10, "max_cat_to_onehot": 4, "metric": "auc", "verbose": -1, "seed": SEED,
    "num_threads": 4,
}


def load_split(path: Path, split: str, neg_frac: float = 1.0, batch: int = 50_000) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    parts = []
    for rb in pq.ParquetFile(path).iter_batches(batch_size=batch):
        df = rb.to_pandas()
        df = df[df["split"] == split]
        if neg_frac < 1.0:
            keep = (df["label"] == 1).to_numpy() | (rng.random(len(df)) < neg_frac)
            df = df[keep]
        parts.append(df)
    out = pd.concat(parts, ignore_index=True)
    for c in CATEGORICAL:
        if c in out:
            out[c] = out[c].astype("category")
    return out


def feature_sets(columns: list[str]) -> dict[str, list[str]]:
    feats = [c for c in columns if c not in META]
    vendor = [c for c in feats if c.startswith("sig_") or c in {f"M{i}" for i in range(1, 10)}]
    return {
        "full": feats,
        "no_streaming": [c for c in feats if c not in STREAMING_FEATURES],
        "no_vendor_signals": [c for c in feats if c not in vendor],
        # what a processor with no upstream risk signals would have: request fields only, then + streaming
        "request_only": [c for c in feats if c not in vendor and c not in STREAMING_FEATURES],
    }


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    fpr, tpr, _ = roc_curve(y, p)
    bins = np.clip((p * 10).astype(int), 0, 9)
    ece = sum(abs(p[bins == b].mean() - y[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any())
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "recall_at_1pct_fpr": float(np.interp(0.01, fpr, tpr)),
        "recall_at_5pct_fpr": float(np.interp(0.05, fpr, tpr)),
        "brier": float(brier_score_loss(y, p)),
        "ece": float(ece),
        "base_rate": float(y.mean()),
    }


def fit_booster(train: pd.DataFrame, valid: pd.DataFrame, cols: list[str]) -> tuple[lgb.Booster, FeatureEncoder]:
    enc = FeatureEncoder.fit(train, cols)
    dtrain = lgb.Dataset(enc.transform_frame(train), train["label"].to_numpy(), feature_name=cols,
                         categorical_feature=enc.categorical, free_raw_data=True)
    dvalid = lgb.Dataset(enc.transform_frame(valid), valid["label"].to_numpy(), reference=dtrain)
    booster = lgb.train(PARAMS, dtrain, num_boost_round=4000, valid_sets=[dvalid],
                        callbacks=[lgb.early_stopping(150, verbose=False), lgb.log_evaluation(500)])
    return booster, enc


def reference_distributions(df: pd.DataFrame, cols: list[str], scores: np.ndarray) -> dict:
    """Binned distributions of the validation month: the baseline that live traffic is compared to (PSI)."""
    ref = {"numeric": {}, "categorical": {}}
    for c in cols:
        if c in CATEGORICAL:
            vc = df[c].astype("string").fillna("<missing>").value_counts(normalize=True)
            ref["categorical"][c] = {str(k): float(v) for k, v in vc.head(30).items()}
        else:
            x = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            finite = x[~np.isnan(x)]
            if len(finite) < 100:
                continue
            edges = np.unique(np.quantile(finite, np.linspace(0, 1, 11)))
            if len(edges) < 3:
                continue
            ref["numeric"][c] = {"edges": edges.tolist(), "props": _bin_props(x, edges)}
    edges = np.unique(np.quantile(scores, np.linspace(0, 1, 11)))
    ref["score"] = {"edges": edges.tolist(), "props": _bin_props(scores, edges)}
    return ref


def _bin_props(x: np.ndarray, edges: np.ndarray) -> list[float]:
    """Proportions over len(edges)-1 interior bins plus a final 'missing' bucket."""
    missing = np.isnan(x)
    idx = np.clip(np.searchsorted(edges, x[~missing], side="right") - 1, 0, len(edges) - 2)
    counts = np.bincount(idx, minlength=len(edges) - 1).astype(float)
    counts = np.append(counts, missing.sum())
    return (counts / max(len(x), 1)).tolist()


def rules_baseline(df: pd.DataFrame, engine: RuleEngine, include_shadow: bool) -> np.ndarray:
    needed = sorted({c.feature for r in engine.rules for c in r.conditions})
    out = []
    for row in df[needed].to_dict("records"):
        out.append(engine.evaluate(row, include_shadow=include_shadow)[0].value)
    return np.array(out, dtype=object)


def rule_stats(df: pd.DataFrame, engine: RuleEngine, model_dec: np.ndarray, review_cost: float) -> dict:
    """Per-rule hit rate, precision, and *marginal* value over the model (hits the model approved)."""
    y, amt = df["label"].to_numpy(), df["amount"].to_numpy()
    out = {}
    for r in engine.rules:
        need = [c.feature for c in r.conditions]
        hit = np.array([r.matches(row) for row in df[need].to_dict("records")], dtype=bool)
        inc = hit & (model_dec == "approve")
        out[r.id] = {
            "mode": r.mode, "hit_rate": float(hit.mean()),
            "precision": float(y[hit].mean()) if hit.any() else None,
            "marginal_hits": int(inc.sum()),
            "marginal_precision": float(y[inc].mean()) if inc.any() else None,
            "marginal_fraud_amount": float(amt[inc & (y == 1)].sum()),
            "marginal_review_cost": float(inc.sum() * review_cost),
        }
    return out


def combine(model_dec: np.ndarray, rule_dec: np.ndarray) -> np.ndarray:
    sev = {Decision.APPROVE.value: 0, Decision.REVIEW.value: 1, Decision.DECLINE.value: 2}
    inv = {v: k for k, v in sev.items()}
    return np.array([inv[max(sev[a], sev[b])] for a, b in zip(model_dec, rule_dec)], dtype=object)


def slices(df: pd.DataFrame, y: np.ndarray, p: np.ndarray) -> dict:
    out = {}
    amount_bucket = pd.cut(df["amount"], [0, 50, 200, 1000, np.inf], labels=["<50", "50-200", "200-1k", ">1k"])
    for name, key in [("product_code", df["product_code"].astype("string").fillna("?")),
                      ("has_identity", df["has_identity"].map({1.0: "yes", 0.0: "no"})),
                      ("amount_bucket", amount_bucket.astype("string"))]:
        out[name] = {}
        for val in sorted(key.dropna().unique()):
            m = (key == val).to_numpy()
            if m.sum() < 200 or y[m].sum() < 10:
                continue
            out[name][str(val)] = {"n": int(m.sum()), "fraud_rate": float(y[m].mean()),
                                   "roc_auc": float(roc_auc_score(y[m], p[m])),
                                   "pr_auc": float(average_precision_score(y[m], p[m]))}
    return out


def save_bundle(out: Path, booster: lgb.Booster, enc: FeatureEncoder, iso: IsotonicRegression, policy,
                reference: dict, metadata: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / "model.txt"), num_iteration=booster.best_iteration or None)
    enc.save(out / "encoder.json")
    (out / "calibration.json").write_text(json.dumps({"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}))
    (out / "policy.json").write_text(json.dumps(policy.to_dict(), indent=2))
    (out / "reference.json").write_text(json.dumps(reference))
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))


def train(ablations: bool = True) -> str:
    s = get_settings()
    path = s.processed_dir / "features.parquet"
    t0 = time.time()
    train_df = load_split(path, "train", neg_frac=NEG_FRAC)
    valid_df = load_split(path, "valid")
    log.info("train rows=%d (pos=%d) valid rows=%d", len(train_df), train_df["label"].sum(), len(valid_df))

    sets = feature_sets(list(train_df.columns))
    cols = sets["full"]
    booster, enc = fit_booster(train_df, valid_df, cols)
    log.info("full model: %d trees in %.0fs", booster.best_iteration, time.time() - t0)

    yv = valid_df["label"].to_numpy()
    raw_v = booster.predict(enc.transform_frame(valid_df), num_iteration=booster.best_iteration)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw_v, yv)
    pv = iso.predict(raw_v)
    pcfg = yaml.safe_load(s.policy_path.read_text())
    policy = fit_policy(pv, yv, valid_df["amount"].to_numpy(), review_capacity=pcfg["review_capacity"],
                        base_review_cost=pcfg["base_review_cost_usd"],
                        decline_target_precision=pcfg["decline_target_precision"])
    fdc = pcfg["false_decline_cost_rate"]
    reference = reference_distributions(valid_df, cols, pv)
    importance = pd.Series(booster.feature_importance("gain"), index=cols).sort_values(ascending=False)
    del train_df

    test_df = load_split(path, "test")
    yt, amt_t = test_df["label"].to_numpy(), test_df["amount"].to_numpy()
    raw_t = booster.predict(enc.transform_frame(test_df), num_iteration=booster.best_iteration)
    pt = iso.predict(raw_t)

    engine = RuleEngine.from_yaml(s.rules_path)
    rules_valid = rule_stats(valid_df, engine, policy.decide_many(pv, valid_df["amount"].to_numpy()),
                             policy.base_review_cost)
    rules_written = rules_baseline(test_df, engine, include_shadow=True)
    rules_enforced = rules_baseline(test_df, engine, include_shadow=False)
    model_dec = policy.decide_many(pt, amt_t)
    report = {
        "test": {"raw": metrics(yt, raw_t), "calibrated": metrics(yt, pt)},
        "valid": {"calibrated": metrics(yv, pv)},
        "policy": policy.to_dict(),
        "decisions_test": {
            "approve_all": policy_outcomes(np.full(len(yt), "approve", dtype=object), yt, amt_t, policy.base_review_cost, fdc),
            "rules_as_written": policy_outcomes(rules_written, yt, amt_t, policy.base_review_cost, fdc),
            "model_only": policy_outcomes(model_dec, yt, amt_t, policy.base_review_cost, fdc),
            "model_plus_all_rules": policy_outcomes(combine(model_dec, rules_written), yt, amt_t, policy.base_review_cost, fdc),
            "model_plus_enforced_rules": policy_outcomes(combine(model_dec, rules_enforced), yt, amt_t,
                                                         policy.base_review_cost, fdc),
        },
        "rules_valid": rules_valid,
        "slices_test": slices(test_df, yt, pt),
        "top_features": [[k, float(v)] for k, v in importance.head(25).items()],
    }

    if ablations:
        report["ablations_test"] = {"full": {k: report["test"]["raw"][k] for k in ("roc_auc", "pr_auc", "recall_at_1pct_fpr")}}
        train_df = load_split(path, "train", neg_frac=NEG_FRAC)
        for name in ("no_streaming", "no_vendor_signals", "request_only"):
            b, e = fit_booster(train_df, valid_df, sets[name])
            m = metrics(yt, b.predict(e.transform_frame(test_df), num_iteration=b.best_iteration))
            report["ablations_test"][name] = {k: m[k] for k in ("roc_auc", "pr_auc", "recall_at_1pct_fpr")}
            log.info("ablation %s: %s", name, report["ablations_test"][name])
        del train_df

    version = "lgbm-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    manifest = json.loads((s.processed_dir / "manifest.json").read_text())
    metadata = {
        "version": version, "created_at": datetime.now(timezone.utc).isoformat(),
        "data": {"source": manifest["source"], "sha256": manifest["sha256"]},
        "warmup_window": "2017-12", "train_window": "2018-01-01..2018-03-31", "valid_window": "2018-04",
        "test_window": "2018-05",
        "neg_downsample": NEG_FRAC, "params": PARAMS, "best_iteration": booster.best_iteration,
        "n_features": len(cols), "report": report, "train_seconds": round(time.time() - t0, 1),
    }
    save_bundle(s.models_dir / version, booster, enc, iso, policy, reference, metadata)
    reg = Registry(s.models_dir)
    if reg.read().get("champion") is None:
        reg.set_alias("champion", version, reason="first model")
    else:
        reg.set_alias("challenger", version, reason="new training run; shadow before promotion")
    log.info("registered %s in %.0fs", version, time.time() - t0)
    return version
