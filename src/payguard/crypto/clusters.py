"""Entity clustering with the common-input-ownership heuristic (Meiklejohn et al., 2013): every input
address of one Bitcoin transaction is controlled by the wallet that signed it, so they are one entity.
This is the core of commercial blockchain analytics: attribution and exposure are tracked per entity,
because individual Bitcoin addresses are mostly single-use (only ~12% of test-period counterparties had
any address-level history here).

Point-in-time: `replay` grows the clustering step by step, so features "as of step s" only use
transactions before s, and illicit labels only after they arrive (label_delay). Serving uses the
final clustering, which is what a live chain index holds "now".

Caveat (also true in industry): CoinJoin transactions break the heuristic by mixing unrelated users'
inputs. Transactions with more than MAX_INPUTS inputs are not unioned, a standard mitigation.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

MAX_INPUTS = 50


class Clusters:
    def __init__(self):
        self.idx: dict[str, int] = {}
        self.parent: list[int] = []
        self.size: list[int] = []
        self.n_tx: list[int] = []
        self.max_risk: list[float] = []
        self.known_illicit: list[int] = []

    def _id(self, address: str) -> int:
        i = self.idx.get(address)
        if i is None:
            i = self.idx[address] = len(self.parent)
            self.parent.append(i)
            self.size.append(1)
            self.n_tx.append(0)
            self.max_risk.append(0.0)
            self.known_illicit.append(0)
        return i

    def find(self, i: int) -> int:
        root = i
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[i] != root:  # path compression
            self.parent[i], i = root, self.parent[i]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        self.n_tx[ra] += self.n_tx[rb]
        self.max_risk[ra] = max(self.max_risk[ra], self.max_risk[rb])
        self.known_illicit[ra] += self.known_illicit[rb]

    def add_tx(self, inputs: list[str], outputs: list[str], risk: float) -> None:
        ids = [self._id(a) for a in inputs]
        if 1 < len(ids) <= MAX_INPUTS:
            for j in ids[1:]:
                self.union(ids[0], j)
        touched = {self.find(i) for i in ids} | {self.find(self._id(a)) for a in outputs}
        for r in touched:
            self.n_tx[r] += 1
            self.max_risk[r] = max(self.max_risk[r], risk)

    def add_label(self, addresses: list[str]) -> None:
        for r in {self.find(self._id(a)) for a in addresses}:
            self.known_illicit[r] += 1

    def features(self, addresses: list[str]) -> dict:
        """Entity view of a set of addresses spent together (the inputs of the transaction being scored):
        by the common-input heuristic they share an owner, so a never-seen address inherits the history of
        any co-input address seen before."""
        roots = {self.find(self.idx[a]) for a in addresses if a in self.idx}
        if not roots:
            return {"cluster_seen": 0.0, "cluster_size": 0.0, "cluster_n_tx": 0.0, "cluster_max_risk": None,
                    "cluster_known_illicit": 0.0}
        return {"cluster_seen": 1.0, "cluster_size": float(sum(self.size[r] for r in roots)),
                "cluster_n_tx": float(sum(self.n_tx[r] for r in roots)),
                "cluster_max_risk": max(self.max_risk[r] for r in roots),
                "cluster_known_illicit": float(sum(self.known_illicit[r] for r in roots))}


def _tx_tables(raw, risk: pd.DataFrame):
    ins = pd.read_csv(raw / "AddrTx_edgelist.csv")
    outs = pd.read_csv(raw / "TxAddr_edgelist.csv")
    tin = ins.groupby("txId")["input_address"].apply(list)
    tout = outs.groupby("txId")["output_address"].apply(list)
    return tin, tout


def replay(raw, risk: pd.DataFrame, queries: dict[int, list[tuple]], label_delay: int) -> tuple[dict, Clusters]:
    """Grow clusters step by step. `queries` maps step -> [(key, addresses)] to describe as of the START of
    that step. Returns ({(step, key): features}, final clusters)."""
    tin, tout = _tx_tables(raw, risk)
    by_step = defaultdict(list)
    for tx, step, label, r in risk[["txId", "step", "label", "risk"]].itertuples(index=False):
        by_step[int(step)].append((tx, int(label), float(r)))
    c = Clusters()
    out: dict = {}
    last = max(by_step)
    for s in range(1, last + 2):
        for key, addresses in queries.get(s, []):
            out[(s, key)] = c.features(addresses)
        for tx, _, r in by_step.get(s, []):  # step s becomes history for step s+1 onwards
            c.add_tx(tin.get(tx, []), tout.get(tx, []), r)
        arrived = s + 1 - label_delay - 1  # labels of step `arrived` are known from step s+1
        for tx, label, _ in by_step.get(arrived, []):
            if label == 1:
                c.add_label(list(tin.get(tx, [])) + list(tout.get(tx, [])))
    return out, c


def to_tables(c: Clusters) -> tuple[pd.DataFrame, pd.DataFrame]:
    roots = np.array([c.find(i) for i in range(len(c.parent))])
    members = pd.DataFrame({"address": list(c.idx.keys()), "cluster_id": roots[list(c.idx.values())]})
    rs = np.unique(roots)
    stats = pd.DataFrame({"cluster_id": rs, "size": np.array(c.size)[rs], "n_tx": np.array(c.n_tx)[rs],
                          "max_risk": np.array(c.max_risk)[rs], "known_illicit": np.array(c.known_illicit)[rs]})
    return members, stats


def cluster_summary(stats: pd.DataFrame) -> dict:
    return {"clusters": int(len(stats)), "largest": int(stats["size"].max()),
            "multi_address_clusters": int((stats["size"] > 1).sum()),
            "addresses_in_multi_address_clusters": int(stats.loc[stats["size"] > 1, "size"].sum()),
            "mean_log_size": float(np.log(stats["size"]).mean()) if math.isfinite(stats["size"].max()) else None}
