"""Read-only investigation tools.

Invariants every tool upholds:
  * point-in-time: only data that existed when the case was raised (`as_of` = the flagged
    transaction's event time). Labels are visible only once they had *arrived* (chargebacks lag).
  * no label leakage: the case's own transaction label is never returned.
  * untrusted text is bounded: strings from customers/merchants (device names, email domains, OCR)
    are truncated and control characters stripped before they reach the LLM.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import sessionmaker

from payguard.db.models import Case, DecisionRecord, Label, Receipt, Transaction
from payguard.explain import describe
from payguard.features.pipeline import STREAMING_FEATURES

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
MAX_STR = 120
ENTITY_COLUMN = {"bin": Transaction.bin_key, "customer": Transaction.customer_key, "device": Transaction.device_key}
PROFILE_PREFIX = {"bin": "bin_", "customer": "customer_", "device": "device_"}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe(v):
    if isinstance(v, str):
        return _CTRL.sub(" ", v)[:MAX_STR]
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, 4)
    if isinstance(v, dict):
        return {k: _safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_safe(x) for x in v]
    return v


class ToolError(Exception):
    pass


@dataclass
class CaseContext:
    case_id: str
    transaction_id: str
    as_of: datetime
    keys: dict[str, str | None]


class InvestigationTools:
    def __init__(self, session_factory: sessionmaker, explainer=None):
        self.sf = session_factory
        self.explainer = explainer

    # ---- context -------------------------------------------------------------------------
    def context(self, case_id: str) -> CaseContext:
        with self.sf() as s:
            row = s.execute(select(Case, Transaction).join(Transaction, Transaction.id == Case.transaction_id)
                            .where(Case.id == case_id)).first()
            if row is None:
                raise ToolError(f"unknown case {case_id}")
            case, tx = row
            return CaseContext(case.id, tx.id, _utc(tx.event_time),
                               {"bin": tx.bin_key, "customer": tx.customer_key, "device": tx.device_key})

    def _labels_known(self, s, ids: list[str], ctx: CaseContext) -> dict[str, bool]:
        if not ids:
            return {}
        rows = s.execute(select(Label.transaction_id, Label.is_fraud)
                         .where(Label.transaction_id.in_(ids), Label.created_at <= ctx.as_of,
                                Label.transaction_id != ctx.transaction_id)).all()
        return dict(rows)

    # ---- tools ---------------------------------------------------------------------------
    def get_case_overview(self, ctx: CaseContext) -> dict:
        with self.sf() as s:
            tx = s.get(Transaction, ctx.transaction_id)
            dec = s.scalar(select(DecisionRecord).where(DecisionRecord.transaction_id == ctx.transaction_id))
            receipts = s.scalars(select(Receipt.id).where(Receipt.case_id == ctx.case_id)).all()
        p = tx.payload
        return _safe({
            "case_id": ctx.case_id, "transaction_id": tx.id, "event_time": ctx.as_of.isoformat(),
            "amount": tx.amount, "currency": p.get("currency"), "product_code": p.get("product_code"),
            "card": {k: p["card"].get(k) for k in ("network", "type")},
            "payer_email_domain": p.get("payer_email_domain"), "recipient_email_domain": p.get("recipient_email_domain"),
            "device": p.get("device"), "billing_region": p["billing"].get("region"),
            "model": {"decision": dec.decision, "fraud_probability": dec.fraud_probability,
                      "expected_loss": dec.expected_loss, "model_version": dec.model_version},
            "model_reasons": [e["detail"] for e in (dec.explanation or (
                self.explainer.explain(tx.id) if self.explainer else None) or [])],
            "rules_triggered": dec.rules,
            "attached_receipts": list(receipts),
        })

    def get_entity_profile(self, ctx: CaseContext, entity: str) -> dict:
        """Streaming-feature snapshot for the entity exactly as the model saw it at decision time."""
        if entity not in PROFILE_PREFIX:
            raise ToolError("entity must be bin, customer or device")
        if ctx.keys[entity] is None:
            return {"entity": entity, "available": False, "note": "no identifier for this entity on the transaction"}
        with self.sf() as s:
            feats = s.scalar(select(DecisionRecord.features).where(DecisionRecord.transaction_id == ctx.transaction_id))
        pre = PROFILE_PREFIX[entity]
        prof = {k[len(pre):]: v for k, v in feats.items() if k.startswith(pre) and k in STREAMING_FEATURES}
        return _safe({"entity": entity, "profile": prof,
                      "readable": [describe(pre + k, v) for k, v in prof.items() if v is not None]})

    def get_entity_history(self, ctx: CaseContext, entity: str, limit: int = 15) -> dict:
        if entity not in ENTITY_COLUMN:
            raise ToolError("entity must be bin, customer or device")
        key = ctx.keys[entity]
        if key is None:
            return {"entity": entity, "available": False}
        limit = max(1, min(int(limit), 50))
        col = ENTITY_COLUMN[entity]
        with self.sf() as s:
            base = and_(col == key, Transaction.event_time < ctx.as_of)
            n, total = s.execute(select(func.count(), func.coalesce(func.sum(Transaction.amount), 0.0))
                                 .where(base)).one()
            rows = s.execute(select(Transaction.id, Transaction.event_time, Transaction.amount,
                                    DecisionRecord.decision, DecisionRecord.fraud_probability)
                             .join(DecisionRecord, DecisionRecord.transaction_id == Transaction.id)
                             .where(base).order_by(Transaction.event_time.desc()).limit(limit)).all()
            labels = self._labels_known(s, [r[0] for r in rows], ctx)
        recent = [{"transaction_id": r[0], "hours_before": round((ctx.as_of - _utc(r[1])).total_seconds() / 3600, 2),
                   "amount": r[2], "decision": r[3], "fraud_probability": r[4],
                   "confirmed_label": None if r[0] not in labels else ("fraud" if labels[r[0]] else "legit")}
                  for r in rows]
        return _safe({"entity": entity, "prior_transactions": n, "prior_amount_total": total,
                      "confirmed_fraud_in_recent": sum(1 for r in recent if r["confirmed_label"] == "fraud"),
                      "confirmed_legit_in_recent": sum(1 for r in recent if r["confirmed_label"] == "legit"),
                      "recent": recent})

    def get_linked_entities(self, ctx: CaseContext) -> dict:
        """1-hop graph: other customers on this device, other devices used by this customer, with the
        confirmed-fraud status of their transactions (a fraud ring shares devices)."""
        out: dict = {}
        with self.sf() as s:
            for src, dst in (("device", "customer"), ("customer", "device")):
                key = ctx.keys[src]
                if key is None:
                    out[f"{dst}s_linked_via_{src}"] = None
                    continue
                src_col, dst_col = ENTITY_COLUMN[src], ENTITY_COLUMN[dst]
                rows = s.execute(select(dst_col, Transaction.id).where(
                    src_col == key, dst_col.is_not(None), Transaction.event_time < ctx.as_of,
                    dst_col != ctx.keys[dst] if ctx.keys[dst] else true()).limit(500)).all()
                labels = self._labels_known(s, [r[1] for r in rows], ctx)
                linked: dict[str, dict] = {}
                for k, tid in rows:
                    d = linked.setdefault(k, {"transactions": 0, "confirmed_fraud": 0})
                    d["transactions"] += 1
                    d["confirmed_fraud"] += int(labels.get(tid, False))
                out[f"{dst}s_linked_via_{src}"] = {
                    "count": len(linked),
                    "with_confirmed_fraud": sum(1 for d in linked.values() if d["confirmed_fraud"]),
                    "sample": [{"id": k[:8], **v} for k, v in list(linked.items())[:10]],
                }
        return _safe(out)

    def find_similar_cases(self, ctx: CaseContext, k: int = 5) -> dict:
        """Nearest labelled past transactions in model-feature space (standardised top features).
        pgvector would replace this scan in production."""
        k = max(1, min(int(k), 10))
        with self.sf() as s:
            me = s.scalar(select(DecisionRecord.features).where(DecisionRecord.transaction_id == ctx.transaction_id))
            rows = s.execute(select(DecisionRecord.transaction_id, DecisionRecord.features, Label.is_fraud,
                                    DecisionRecord.fraud_probability)
                             .join(Label, Label.transaction_id == DecisionRecord.transaction_id)
                             .join(Transaction, Transaction.id == DecisionRecord.transaction_id)
                             .where(Label.created_at <= ctx.as_of, Transaction.event_time < ctx.as_of,
                                    DecisionRecord.transaction_id != ctx.transaction_id)
                             .order_by(Transaction.event_time.desc()).limit(3000)).all()
        if not rows:
            return {"similar": [], "note": "no labelled history available yet"}
        cols = [c for c in SIMILARITY_FEATURES if c in me]
        X = np.array([[_num(r[1].get(c)) for c in cols] for r in rows], dtype=float)
        q = np.array([_num(me.get(c)) for c in cols], dtype=float)
        mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0) + 1e-6
        Z, qz = (X - mu) / sd, (q - mu) / sd
        diff = np.where(np.isnan(Z) | np.isnan(qz), 1.0, Z - qz)  # missing on either side: fixed penalty
        dist = np.sqrt((diff ** 2).mean(axis=1))
        idx = np.argsort(dist)[:k]
        sims = [{"transaction_id": rows[i][0], "distance": round(float(dist[i]), 3),
                 "label": "fraud" if rows[i][2] else "legit", "model_probability": round(rows[i][3], 4)} for i in idx]
        return {"pool_size": len(rows), "similar": sims,
                "fraud_share_among_similar": round(sum(x["label"] == "fraud" for x in sims) / len(sims), 3)}

    def get_receipt_verification(self, ctx: CaseContext, receipt_id: str) -> dict:
        with self.sf() as s:
            r = s.get(Receipt, receipt_id)
            if r is None or r.case_id != ctx.case_id:
                raise ToolError("no such receipt on this case")
            return _safe({"receipt_id": r.id, "verdict": r.verdict, "claimed_reference": r.claimed_reference,
                          "checks": r.result.get("checks"), "extracted": r.result.get("extracted")})


SIMILARITY_FEATURES = [
    "log_amount", "hour", "customer_cnt_24h", "customer_cnt_7d", "customer_amt_z", "customer_is_new",
    "customer_age_days", "bin_cnt_1h", "bin_cnt_24h", "device_n_distinct_customer", "customer_n_distinct_device",
    "sig_C1", "sig_C13", "sig_C14", "sig_D1", "sig_D15", "distance", "has_identity", "email_match",
]


def _num(v) -> float:
    try:
        return float(v) if v is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")
