"""On-chain transaction risk model (Bitcoin, Elliptic++), evaluated the way it would be deployed.

Protocol (strict-inductive, out-of-time):
  train steps 1-34 | valid 35-39 (threshold + isotonic calibration) | test 40-49, touched once.
  Test is also split at step 43, when a large dark market shut down and published models collapse.

Feature sets compared:
  local        the transaction's own features (Elliptic local + Elliptic++ augmented)
  local+agg    + Elliptic's 1-hop neighbour aggregates
  +graph       + degree and neighbour *model scores* (mean/max over in/out neighbours). Neighbours are
               same-time-step transactions only; train-period scores are out-of-fold (grouped by time
               step), so no node ever sees a label it was trained on through the graph. This is the
               "GNN-lite" that a recent re-evaluation (arXiv:2604.19514) suggests is safer than GNNs
               trained with test-period adjacency.
  RF           Random Forest on local+agg, the baseline that paper found beats GraphSAGE.

Outputs: a model bundle under artifacts/models/crypto/<version>/ and out-of-sample risk scores for every
transaction (feeding the address intelligence store).
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, f1_score, precision_recall_curve, roc_auc_score

log = logging.getLogger(__name__)

TRAIN_END, VALID_END, SHUTDOWN = 34, 39, 43
PARAMS = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 30,
          "feature_fraction": 0.5, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
          "metric": "average_precision", "verbose": -1, "seed": 7, "num_threads": 4}


def feature_groups(df: pd.DataFrame) -> dict[str, list[str]]:
    local = [c for c in df.columns if c.startswith("Local_feature_")]
    agg = [c for c in df.columns if c.startswith("Aggregate_feature_")]
    extra = [c for c in df.columns if c not in local + agg + ["txId", "step", "label"]]
    return {"local": local + extra, "agg": agg}


def graph_frame(df: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    idx = pd.Series(np.arange(len(df)), index=df["txId"].to_numpy())
    e = edges[edges["txId1"].isin(idx.index) & edges["txId2"].isin(idx.index)]
    src, dst = idx[e["txId1"]].to_numpy(), idx[e["txId2"]].to_numpy()
    n = len(df)
    out = pd.DataFrame(index=df.index)
    out["g_in_degree"] = np.bincount(dst, minlength=n).astype(np.float32)
    out["g_out_degree"] = np.bincount(src, minlength=n).astype(np.float32)
    out.attrs["edges"] = (src, dst)
    return out


def neighbour_scores(scores: np.ndarray, src: np.ndarray, dst: np.ndarray) -> pd.DataFrame:
    n = len(scores)
    out = {}
    for name, a, b in (("in", dst, src), ("out", src, dst)):  # "in": predecessors of a node
        s = np.zeros(n)
        np.add.at(s, a, scores[b])
        cnt = np.bincount(a, minlength=n)
        mx = np.zeros(n)
        np.maximum.at(mx, a, scores[b])
        out[f"g_{name}_nbr_mean"] = np.where(cnt > 0, s / np.maximum(cnt, 1), np.nan)
        out[f"g_{name}_nbr_max"] = np.where(cnt > 0, mx, np.nan)
    return pd.DataFrame(out).astype(np.float32)


def _fit(X, y, Xv, yv, rounds=3000):
    d = lgb.Dataset(X, y)
    dv = lgb.Dataset(Xv, yv, reference=d)
    return lgb.train(PARAMS, d, rounds, valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])


def oof_scores(df: pd.DataFrame, cols: list[str], folds: int = 5) -> np.ndarray:
    """Out-of-sample risk for EVERY transaction: train-period rows get out-of-fold predictions (folds are
    groups of time steps), later rows get the model trained on the full training period."""
    lab = df["label"] >= 0
    tr = (df["step"] <= TRAIN_END) & lab
    va = (df["step"] > TRAIN_END) & (df["step"] <= VALID_END) & lab
    scores = np.zeros(len(df))
    steps = np.arange(1, TRAIN_END + 1)
    for k in range(folds):
        held = steps[k::folds]
        fit_m = tr & ~df["step"].isin(held)
        b = _fit(df.loc[fit_m, cols], df.loc[fit_m, "label"], df.loc[va, cols], df.loc[va, "label"])
        rows = (df["step"] <= TRAIN_END) & df["step"].isin(held)
        scores[rows.to_numpy()] = b.predict(df.loc[rows, cols], num_iteration=b.best_iteration)
    full = _fit(df.loc[tr, cols], df.loc[tr, "label"], df.loc[va, cols], df.loc[va, "label"])
    later = (df["step"] > TRAIN_END).to_numpy()
    scores[later] = full.predict(df.loc[later, cols], num_iteration=full.best_iteration)
    return scores


REVIEW_PRECISION, BLOCK_PRECISION = 0.50, 0.95


def precision_threshold(y: np.ndarray, p: np.ndarray, target: float, min_flagged: int = 20) -> float:
    """Lowest score threshold whose flagged set (p >= t) reaches `target` precision on validation data.
    Review needs half of flags to be real; blocking (which hurts a customer without a human) needs 95%."""
    prec, _, th = precision_recall_curve(y, p)
    flagged = np.array([(p >= t).sum() for t in th])
    ok = np.where((prec[:-1] >= target) & (flagged >= min_flagged))[0]
    return float(th[ok[0]]) if len(ok) else 1.0


def review_block_thresholds(y: np.ndarray, p: np.ndarray) -> dict:
    review = precision_threshold(y, p, REVIEW_PRECISION)
    block = max(precision_threshold(y, p, BLOCK_PRECISION), review)  # invariant: review <= block
    return {"review": review, "block": block, "review_precision_target": REVIEW_PRECISION,
            "block_precision_target": BLOCK_PRECISION}


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-12)
    return float(thr[np.argmax(f1[:-1])])


def metrics(y: np.ndarray, p: np.ndarray, thr: float) -> dict:
    yhat = p >= thr
    return {"illicit_f1": float(f1_score(y, yhat)), "pr_auc": float(average_precision_score(y, p)),
            "roc_auc": float(roc_auc_score(y, p)), "precision": float(y[yhat].mean()) if yhat.any() else 0.0,
            "recall": float(yhat[y == 1].mean()), "n": int(len(y)), "illicit": int(y.sum())}


def train(processed: Path, raw: Path, models_dir: Path) -> str:
    t0 = time.time()
    df = pd.read_parquet(processed / "crypto_txs.parquet")
    groups = feature_groups(df)
    g = graph_frame(df, pd.read_csv(raw / "txs_edgelist.csv"))
    src, dst = g.attrs["edges"]
    df = pd.concat([df, g], axis=1)
    sets = {"local": groups["local"], "local+agg": groups["local"] + groups["agg"]}

    lab = (df["label"] >= 0).to_numpy()
    tr = lab & (df["step"] <= TRAIN_END).to_numpy()
    va = lab & ((df["step"] > TRAIN_END) & (df["step"] <= VALID_END)).to_numpy()
    te = lab & (df["step"] > VALID_END).to_numpy()
    y = df["label"].to_numpy()
    steps = df["step"].to_numpy()

    # Stage 1 out-of-sample scores -> neighbour-score features (inductive by construction)
    stage1 = oof_scores(df, sets["local+agg"])
    nb = neighbour_scores(stage1, src, dst)
    df = pd.concat([df, nb], axis=1)
    sets["local+agg+graph"] = sets["local+agg"] + ["g_in_degree", "g_out_degree"] + list(nb.columns)

    report: dict = {"protocol": {"train_steps": [1, TRAIN_END], "valid_steps": [TRAIN_END + 1, VALID_END],
                                 "test_steps": [VALID_END + 1, 49], "dark_market_shutdown_step": SHUTDOWN},
                    "variants": {}}
    best = None
    for name, cols in sets.items():
        b = _fit(df.loc[tr, cols], y[tr], df.loc[va, cols], y[va])
        pv = b.predict(df.loc[va, cols], num_iteration=b.best_iteration)
        thr = best_f1_threshold(y[va], pv)
        pt = b.predict(df.loc[te, cols], num_iteration=b.best_iteration)
        report["variants"][f"lightgbm[{name}]"] = _evaluate(y, steps, te, pt, thr, valid=metrics(y[va], pv, thr))
        log.info("%s test: %s", name, report["variants"][f"lightgbm[{name}]"]["test"])
        score = report["variants"][f"lightgbm[{name}]"]["valid"]["pr_auc"]
        if best is None or score > best[0]:
            best = (score, name, b, cols, pv, thr)

    rf = RandomForestClassifier(n_estimators=300, max_features=50, min_samples_leaf=2, n_jobs=4, random_state=7)
    cols = sets["local+agg"]
    rf.fit(df.loc[tr, cols].fillna(-1), y[tr])
    pv = rf.predict_proba(df.loc[va, cols].fillna(-1))[:, 1]
    thr = best_f1_threshold(y[va], pv)
    pt = rf.predict_proba(df.loc[te, cols].fillna(-1))[:, 1]
    report["variants"]["random_forest[local+agg]"] = _evaluate(y, steps, te, pt, thr, valid=metrics(y[va], pv, thr))
    log.info("RF test: %s", report["variants"]["random_forest[local+agg]"]["test"])

    _, name, booster, cols, pv, thr = best
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(pv, y[va])
    thresholds = review_block_thresholds(y[va], iso.predict(pv))

    version = "crypto-lgbm-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = models_dir / "crypto" / version
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / "model.txt"), num_iteration=booster.best_iteration)
    (out / "features.json").write_text(json.dumps(cols))
    (out / "calibration.json").write_text(json.dumps({"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}))
    (out / "thresholds.json").write_text(json.dumps(thresholds))
    report["champion"] = {"variant": f"lightgbm[{name}]", "thresholds": thresholds}
    report["seconds"] = round(time.time() - t0, 1)
    (out / "metadata.json").write_text(json.dumps({"version": version, "report": report}, indent=2))

    # Out-of-sample risk for every transaction, calibrated, for the address intelligence store.
    risk = oof_scores(df, cols)
    pd.DataFrame({"txId": df["txId"], "step": df["step"], "label": df["label"],
                  "risk": iso.predict(risk).astype(np.float32)}).to_parquet(out / "tx_risk.parquet", index=False)
    (models_dir / "crypto" / "champion.txt").write_text(version)
    log.info("crypto model %s (%s) in %.0fs", version, name, time.time() - t0)
    return version


def _evaluate(y, steps, te, pt, thr, valid) -> dict:
    pre = te & (steps < SHUTDOWN)
    post = te & (steps >= SHUTDOWN)
    test_idx = np.where(te)[0]
    p_full = np.full(len(y), np.nan)
    p_full[test_idx] = pt
    by_step = {}
    for s in range(VALID_END + 1, 50):
        m = te & (steps == s)
        if m.any() and y[m].sum() > 0:
            by_step[s] = round(float(f1_score(y[m], p_full[m] >= thr)), 4)
    return {"valid": valid, "test": metrics(y[te], pt, thr),
            "test_pre_shutdown": metrics(y[pre], p_full[pre], thr),
            "test_post_shutdown": metrics(y[post], p_full[post], thr) if y[post].sum() else None,
            "f1_by_step": by_step, "threshold": thr}
