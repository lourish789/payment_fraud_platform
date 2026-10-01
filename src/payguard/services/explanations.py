"""Model explanations, computed OFF the request path.

TreeSHAP over this model (~1k trees) costs 60-100 ms per transaction, against ~1 ms for the prediction
itself; inline, it alone would break the scoring latency budget. Explanations are only needed by analysts
and the agent, and only for flagged transactions, so they are computed when a case is created (worker) or
on first read, and cached on the decision record. Recomputation is exact: the stored serving-time feature snapshot is re-encoded by the
same model version that made the decision.

Merchants get a decision and rule codes, never model internals: detailed reason codes returned to the
client side help fraudsters probe the model.
"""

from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from payguard.db.models import DecisionRecord
from payguard.db.session import write_guard
from payguard.explain import describe
from payguard.models.registry import ModelBundle, Registry


class Explainer:
    def __init__(self, session_factory: sessionmaker, registry: Registry, top: int = 5):
        self.sf, self.registry, self.top = session_factory, registry, top
        self._bundles: dict[str, ModelBundle] = {}
        self._lock = threading.Lock()

    def _bundle(self, version: str) -> ModelBundle:
        with self._lock:
            if version not in self._bundles:
                self._bundles[version] = ModelBundle.load(self.registry.root / version)
            return self._bundles[version]

    def explain(self, transaction_id: str) -> list[dict] | None:
        with self.sf() as s:
            dec = s.scalar(select(DecisionRecord).where(DecisionRecord.transaction_id == transaction_id))
            if dec is None:
                return None
            if dec.explanation is not None:
                return dec.explanation
            version, feats = dec.model_version, dec.features
        bundle = self._bundle(version)
        x = bundle.encoder.transform_row(feats)
        expl = [{"feature": f, "detail": describe(f, feats.get(f)), "weight": round(w, 4)}
                for f, w in bundle.contributions(x, top=self.top)]
        with write_guard(self.sf), self.sf() as s, s.begin():
            s.get(DecisionRecord, dec.id).explanation = expl
        return expl
