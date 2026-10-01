"""Point-in-time address intelligence: what we knew about a counterparty address at the time of a payment.

This mirrors how KYT/blockchain-analytics providers work (index the chain continuously, attribute
addresses, propagate exposure), with one rule enforced everywhere: a query "as of time step q" only
sees transactions before q, and only sees an illicit *label* once it has arrived (label_delay steps
later), because attribution in practice lags the on-chain activity.

Store (SQLite here; a production deployment would use Postgres or a key-value store):
  tx_risk(txId, step, risk)                          out-of-sample model risk of every indexed transaction
  addr_step(address, step, n_tx, max_risk, n_illicit) per-address activity per time step
  pairs(address, counterparty, step)                  1-hop counterparties via shared transactions
  addr_status(address, first_illicit_step)            first time step the address was in an illicit tx

Counterparty risk = a small logistic combiner over these signals, trained on the validation period and
evaluated on the test period (both out-of-sample for the transaction model).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, precision_recall_curve, roc_auc_score

from payguard.crypto import clusters
from payguard.crypto.model import review_block_thresholds

log = logging.getLogger(__name__)

LABEL_DELAY = 1  # steps (~2 weeks) before an illicit attribution becomes known
MAX_PAIRS_PER_TX = 400  # skip in x out pair expansion for huge consolidation/batch transactions
VALID_STEPS, TEST_STEPS = range(35, 40), range(40, 50)
COMBINER_FEATURES = ["tx_risk", "tx_risk_known", "addr_seen", "log_prior_txs", "addr_max_prior_risk",
                     "addr_known_illicit", "log_counterparties", "illicit_cp_share", "illicit_cp_any",
                     "cluster_seen", "log_cluster_size", "cluster_max_risk", "cluster_known_illicit"]
SUBSETS = {"tx_model_only": [0, 1], "address_history_only": [2, 3, 4, 5, 6, 7, 8], "entity_cluster_only": [9, 10, 11, 12],
           "combined": list(range(13))}


def build(model_dir: Path, raw: Path, db_path: Path) -> dict:
    t0 = time.time()
    risk = pd.read_parquet(model_dir / "tx_risk.parquet")
    ins = pd.read_csv(raw / "AddrTx_edgelist.csv").rename(columns={"input_address": "address"})
    outs = pd.read_csv(raw / "TxAddr_edgelist.csv").rename(columns={"output_address": "address"})
    edges = pd.concat([ins.assign(role="in"), outs.assign(role="out")], ignore_index=True)
    edges = edges.merge(risk, on="txId", how="inner")

    addr_step = (edges.groupby(["address", "step"])
                 .agg(n_tx=("txId", "nunique"), max_risk=("risk", "max"),
                      n_illicit=("label", lambda s: int((s == 1).sum()))).reset_index())
    status = (edges[edges["label"] == 1].groupby("address")["step"].min()
              .rename("first_illicit_step").reset_index())

    # 1-hop counterparties: every input address of a tx paired with every output address (both directions)
    sizes = edges.groupby(["txId", "role"]).size().unstack(fill_value=0)
    ok = sizes.index[(sizes.get("in", 0) * sizes.get("out", 0)).between(1, MAX_PAIRS_PER_TX)]
    e_ok = edges[edges["txId"].isin(ok)]
    pairs = e_ok[e_ok.role == "in"][["txId", "address", "step"]].merge(
        e_ok[e_ok.role == "out"][["txId", "address"]].rename(columns={"address": "counterparty"}), on="txId")
    pairs = pairs[pairs.address != pairs.counterparty][["address", "counterparty", "step"]]
    pairs = pd.concat([pairs, pairs.rename(columns={"address": "counterparty", "counterparty": "address"})])
    pairs = pairs.drop_duplicates()

    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    risk[["txId", "step", "risk"]].to_sql("tx_risk", con, index=False, chunksize=50_000)
    addr_step.to_sql("addr_step", con, index=False, chunksize=50_000)
    pairs.to_sql("pairs", con, index=False, chunksize=100_000)
    status.to_sql("addr_status", con, index=False, chunksize=50_000)
    _, final = clusters.replay(raw, risk, {}, LABEL_DELAY)
    members, cstats = clusters.to_tables(final)
    members.to_sql("addr_cluster", con, index=False, chunksize=100_000)
    ins[["txId", "address"]].to_sql("tx_inputs", con, index=False, chunksize=100_000)
    cstats.to_sql("cluster_stats", con, index=False, chunksize=100_000)
    con.executescript("""
        CREATE UNIQUE INDEX ix_addr_cluster ON addr_cluster(address);
        CREATE INDEX ix_tx_inputs ON tx_inputs(txId);
        CREATE UNIQUE INDEX ix_cluster_stats ON cluster_stats(cluster_id);
        CREATE UNIQUE INDEX ix_tx ON tx_risk(txId);
        CREATE INDEX ix_addr_step ON addr_step(address, step);
        CREATE INDEX ix_pairs ON pairs(address, step);
        CREATE UNIQUE INDEX ix_status ON addr_status(address);
    """)
    con.commit()
    con.close()
    stats = {"addresses": int(addr_step.address.nunique()), "addr_step_rows": len(addr_step),
             "clustering": clusters.cluster_summary(cstats),
             "pairs": len(pairs), "txs_skipped_for_pairs": int(len(sizes) - len(ok)),
             "seconds": round(time.time() - t0, 1)}
    log.info("address intel built: %s", stats)
    return stats


@dataclass
class AddressIntel:
    con: sqlite3.Connection

    @classmethod
    def open(cls, db_path: Path) -> "AddressIntel":
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        return cls(con)

    def profile(self, address: str, as_of_step: int, tx_id: int | None = None, label_delay: int = LABEL_DELAY) -> dict:
        """Signals about `address`. Address-level fields use only information before `as_of_step`; entity
        (cluster) fields come from the live clustering, i.e. "as of now", which is correct when serving.
        Offline evaluation replaces them with point-in-time values from clusters.replay."""
        c = self.con
        n_steps, prior_txs, max_risk, first_seen = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(n_tx),0), MAX(max_risk), MIN(step) FROM addr_step "
            "WHERE address=? AND step<?", (address, as_of_step)).fetchone()
        known_illicit = c.execute(
            "SELECT COALESCE(SUM(n_illicit),0) FROM addr_step WHERE address=? AND step<=?",
            (address, as_of_step - 1 - label_delay)).fetchone()[0]
        n_cp, n_bad_cp = c.execute(
            "SELECT COUNT(DISTINCT p.counterparty), "
            "COUNT(DISTINCT CASE WHEN s.first_illicit_step <= ? THEN p.counterparty END) "
            "FROM pairs p LEFT JOIN addr_status s ON s.address = p.counterparty "
            "WHERE p.address=? AND p.step<?", (as_of_step - 1 - label_delay, address, as_of_step)).fetchone()
        tx_risk = None
        if tx_id is not None:
            row = c.execute("SELECT risk FROM tx_risk WHERE txId=?", (int(tx_id),)).fetchone()
            tx_risk = row[0] if row else None
        spent_with = [address]
        if tx_id is not None:
            spent_with += [r[0] for r in c.execute("SELECT address FROM tx_inputs WHERE txId=?", (int(tx_id),))]
        marks = ",".join("?" * len(spent_with))
        rows = c.execute(f"SELECT DISTINCT s.cluster_id, s.size, s.n_tx, s.max_risk, s.known_illicit FROM addr_cluster a "
                         f"JOIN cluster_stats s ON s.cluster_id = a.cluster_id WHERE a.address IN ({marks})",
                         spent_with).fetchall()
        cluster = ({"cluster_seen": 1.0, "cluster_size": float(sum(r[1] for r in rows)),
                    "cluster_n_tx": float(sum(r[2] for r in rows)), "cluster_max_risk": max(r[3] for r in rows),
                    "cluster_known_illicit": float(sum(r[4] for r in rows))} if rows else
                   {"cluster_seen": 0.0, "cluster_size": 0.0, "cluster_n_tx": 0.0, "cluster_max_risk": None,
                    "cluster_known_illicit": 0.0})
        return {
            **cluster,
            "tx_risk": tx_risk,
            "addr_seen": float(n_steps > 0),
            "prior_txs": float(prior_txs),
            "first_seen_step": first_seen,
            "addr_max_prior_risk": max_risk,
            "addr_known_illicit": float(known_illicit),
            "counterparties": float(n_cp),
            "illicit_counterparties": float(n_bad_cp),
        }


def combiner_row(prof: dict) -> list[float]:
    """Fixed, documented transform of a profile into combiner inputs (missing -> neutral 0)."""
    n_cp = prof["counterparties"]
    return [
        prof["tx_risk"] if prof["tx_risk"] is not None else 0.0,
        float(prof["tx_risk"] is not None),
        prof["addr_seen"],
        math.log1p(prof["prior_txs"]),
        prof["addr_max_prior_risk"] if prof["addr_max_prior_risk"] is not None else 0.0,
        float(prof["addr_known_illicit"] > 0),
        math.log1p(n_cp),
        prof["illicit_counterparties"] / n_cp if n_cp else 0.0,
        float(prof["illicit_counterparties"] > 0),
        prof["cluster_seen"],
        math.log1p(prof["cluster_size"]),
        prof["cluster_max_risk"] if prof["cluster_max_risk"] is not None else 0.0,
        float(prof["cluster_known_illicit"] > 0),
    ]


def counterparty_events(raw: Path, model_dir: Path, steps) -> pd.DataFrame:
    """Evaluation events: 'a deposit from address A carried by labelled transaction T at step s'.
    Truth is T's Elliptic label. ONE event per transaction (its lexicographically first input address):
    weighting by input count would let a few huge transactions (one has 497 inputs) dominate the metric."""
    risk = pd.read_parquet(model_dir / "tx_risk.parquet")
    ins = pd.read_csv(raw / "AddrTx_edgelist.csv").rename(columns={"input_address": "address"})
    ev = ins.merge(risk[risk.label >= 0], on="txId")
    ev = ev[ev.step.isin(list(steps))]
    return ev.sort_values(["txId", "address"]).drop_duplicates("txId")


def fit_combiner(intel: AddressIntel, raw: Path, model_dir: Path, out: Path, max_events: int = 40_000) -> dict:
    """Train on validation-period events, evaluate on test-period events, compare signal subsets."""
    rng = np.random.default_rng(0)
    events = {}
    for name, steps in (("valid", VALID_STEPS), ("test", TEST_STEPS)):
        ev = counterparty_events(raw, model_dir, steps)
        if len(ev) > max_events:
            ev = ev.iloc[rng.choice(len(ev), max_events, replace=False)]
        events[name] = ev
    inputs = pd.read_csv(raw / "AddrTx_edgelist.csv").groupby("txId")["input_address"].apply(list)
    queries: dict[int, list[tuple]] = {}
    for ev in events.values():
        for a, st_, t in zip(ev.address, ev.step, ev.txId):
            queries.setdefault(int(st_), []).append(((a, int(t)), [a] + inputs.get(t, [])))
    pit, _ = clusters.replay(raw, pd.read_parquet(model_dir / "tx_risk.parquet"), queries, LABEL_DELAY)

    def frame(ev):
        rows = []
        for a, s_, t in zip(ev.address, ev.step, ev.txId):
            prof = intel.profile(a, int(s_), int(t))
            prof.update(pit[(int(s_), (a, int(t)))])  # point-in-time entity features, not the live clustering
            rows.append(combiner_row(prof))
        return np.array(rows, dtype=float), ev.label.to_numpy(), ev.step.to_numpy()

    Xv, yv, _ = frame(events["valid"])
    Xt, yt, st = frame(events["test"])
    subsets = SUBSETS
    report = {"label_delay_steps": LABEL_DELAY, "n_valid_events": int(len(yv)), "n_test_events": int(len(yt)),
              "test_illicit_share": float(yt.mean()),
              "test_coverage_address_seen_before": float(Xt[:, 2].mean()),
              "test_coverage_entity_seen_before": float(Xt[:, 9].mean()), "variants": {}}
    final = None
    for name, idx in subsets.items():
        lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Xv[:, idx], yv)
        pv, pt = lr.predict_proba(Xv[:, idx])[:, 1], lr.predict_proba(Xt[:, idx])[:, 1]
        prec, rec, thr = precision_recall_curve(yv, pv)
        f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-12)
        t = float(thr[np.argmax(f1[:-1])])
        pre, post = st < 43, st >= 43
        report["variants"][name] = {
            "test_pr_auc": float(average_precision_score(yt, pt)), "test_roc_auc": float(roc_auc_score(yt, pt)),
            "test_f1": float(f1_score(yt, pt >= t)),
            "test_f1_pre_shutdown": float(f1_score(yt[pre], pt[pre] >= t)),
            "test_f1_post_shutdown": float(f1_score(yt[post], pt[post] >= t)) if yt[post].sum() else None,
            "threshold": t}
        if name == "combined":
            final = (lr, pv, t)
    lr, pv, t = final
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(pv, yv)
    thr = review_block_thresholds(yv, iso.predict(pv))
    assert thr["review"] <= thr["block"]
    params = {"features": COMBINER_FEATURES, "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]),
              "calibration": {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()},
              "review_threshold": thr["review"], "block_threshold": thr["block"]}
    # Operating points on the test period, at the thresholds fixed on validation
    pt_cal = iso.predict(lr.predict_proba(Xt)[:, 1])
    for band, cut in (("review", thr["review"]), ("block", thr["block"])):
        flag = pt_cal >= cut
        report[f"test_at_{band}_threshold"] = {"threshold": cut, "flag_rate": float(flag.mean()),
                                               "precision": float(yt[flag].mean()) if flag.any() else None,
                                               "recall": float(flag[yt == 1].mean())}
    out.write_text(json.dumps({"combiner": params, "report": report}, indent=2))
    return report


class CounterpartyRisk:
    """Serving-side combiner: profile -> calibrated probability the counterparty flow is illicit."""

    def __init__(self, params: dict):
        self.p = params
        self.coef = np.array(params["coef"])
        self.x, self.y = np.array(params["calibration"]["x"]), np.array(params["calibration"]["y"])

    @classmethod
    def load(cls, path: Path) -> "CounterpartyRisk":
        return cls(json.loads(path.read_text())["combiner"])

    def score(self, prof: dict) -> float:
        z = float(np.dot(self.coef, combiner_row(prof)) + self.p["intercept"])
        return float(np.interp(1 / (1 + math.exp(-z)), self.x, self.y))
