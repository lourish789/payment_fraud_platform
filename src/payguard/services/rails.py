"""Per-rail risk assessment. Each payment rail plugs into the shared scoring service (idempotency,
persistence, cases, outbox, agent) through `assess`, so every payment method ends up in the same
decision record and the same analyst queue.

  card            calibrated LightGBM (IEEE-CIS) + expected-loss policy + rules       (models/)
  bank_transfer   scorecard (APP-scam / mule signals) + rules                          (configs/rails/)
  mobile_money    scorecard (SIM swap, cash-out, mule signals) + rules                 (configs/rails/)
  crypto          scorecard + OFAC screening + counterparty risk (Elliptic-trained) + Travel Rule

Scorecards: log-odds points per condition, p = sigmoid(intercept + sum(points)). They are the honest
starting point for rails without public labelled data: transparent, reviewable by compliance, and
replaced by a trained model once analyst resolutions and chargebacks accumulate in the labels table.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from payguard import observability as obs
from payguard.crypto.data import step_of
from payguard.features import rails as rf
from payguard.features.pipeline import FeaturePipeline, entity_keys
from payguard.rules import OPS, Condition, RuleEngine, most_severe
from payguard.schemas import Decision, ReasonCode

log = logging.getLogger(__name__)


@dataclass
class Assessment:
    features: dict
    p: float
    raw: float
    model_version: str
    decision: Decision
    reasons: list[ReasonCode]
    rules: list[str]
    keys: dict
    expected_loss: float
    actions: list[str] = field(default_factory=list)
    shadow: dict | None = None
    degraded: bool = False
    explanation: list[dict] | None = None  # set when cheap to compute at scoring time (scorecards)


def _hash(v: str | None) -> str | None:
    return hashlib.blake2b(v.encode(), digest_size=8).hexdigest() if v else None


def _device_key(device) -> str | None:
    parts = (device.info, device.os, device.browser, device.screen)
    return _hash("|".join(x or "" for x in parts)) if sum(x is not None for x in parts) >= 2 else None


class CardScorer:
    rail = "card"

    def __init__(self, pipeline: FeaturePipeline, models, rules: RuleEngine):
        self.pipeline, self.models, self.rules = pipeline, models, rules

    def assess(self, txn) -> Assessment:
        champion, challenger = self.models.current()
        if champion is None:
            raise RuntimeError("no champion model registered")
        degraded = False
        try:
            feats, _ = self.pipeline.build(txn)
        except Exception:
            # Fail soft: an online-store outage must not stop payments (see RUNBOOK)
            log.exception("online feature store unavailable; scoring degraded")
            obs.DEGRADED.inc()
            feats, degraded = self.pipeline.build_degraded(txn), True
        _, raw, p = champion.predict_row(feats)
        model_decision = champion.policy.decide(p, txn.amount)
        rule_decision, hits = self.rules.evaluate(feats)
        reasons = []
        if model_decision != Decision.APPROVE:
            reasons.append(ReasonCode(code="model_risk", detail="Elevated fraud risk", weight=round(p, 4)))
        reasons += [ReasonCode(code=f"rule:{r.id}", detail=r.description) for r in hits if r.mode == "enforce"]
        if degraded:
            reasons.append(ReasonCode(code="degraded", detail="Behavioural history unavailable; scored on request data only"))
        shadow = None
        if challenger is not None:
            _, _, s_p = challenger.predict_row(feats)
            shadow = {"version": challenger.version, "fraud_probability": s_p,
                      "decision": challenger.policy.decide(s_p, txn.amount).value}
        for r in hits:
            obs.RULE_HITS.labels(r.id, r.mode).inc()
        keys = entity_keys(txn)
        return Assessment(feats, p, raw, champion.version, most_severe(model_decision, rule_decision), reasons,
                          [r.id if r.mode == "enforce" else f"shadow:{r.id}" for r in hits],
                          {"bin_key": keys["bin"], "customer_key": keys["customer"], "device_key": keys["device"],
                           "counterparty_key": None},
                          p * txn.amount, shadow=shadow, degraded=degraded)


@dataclass(frozen=True)
class Points:
    cond: Condition
    points: float
    reason: str


class Scorecard:
    def __init__(self, cfg: dict):
        self.version = cfg["version"]
        self.intercept = float(cfg["intercept"])
        self.review, self.decline = float(cfg["thresholds"]["review"]), float(cfg["thresholds"]["decline"])
        self.items = []
        for w in cfg["weights"]:
            if w["op"] not in OPS:
                raise ValueError(f"scorecard {self.version}: unsupported op {w['op']}")
            self.items.append(Points(Condition(w["feature"], w["op"], w["value"]), float(w["points"]), w["reason"]))

    def score(self, feats: dict) -> tuple[float, float, list[dict]]:
        hits = [it for it in self.items if it.cond.holds(feats)]
        z = self.intercept + sum(it.points for it in hits)
        contrib = [{"feature": it.cond.feature, "detail": it.reason, "weight": it.points}
                   for it in sorted(hits, key=lambda it: -abs(it.points))]
        return 1 / (1 + math.exp(-z)), z, contrib

    def decide(self, p: float) -> Decision:
        return Decision.DECLINE if p >= self.decline else Decision.REVIEW if p >= self.review else Decision.APPROVE


class ScorecardScorer:
    def __init__(self, rail: str, store, config_dir: Path, travel_rule_threshold_usd: float = 1000.0):
        cfg = yaml.safe_load((config_dir / f"{rail}.yaml").read_text())
        self.rail, self.store = rail, store
        self.card = Scorecard(cfg["scorecard"])
        self.rules = RuleEngine.from_dict(cfg.get("rules", []))
        self.travel_rule_threshold_usd = travel_rule_threshold_usd

    def features(self, payment) -> tuple[dict, bool]:
        feats = rf.request_features(payment, self.travel_rule_threshold_usd)
        degraded = False
        try:
            streaming, _ = rf.streaming_features(self.store, payment)
        except Exception:
            log.exception("online feature store unavailable; %s scoring degraded", self.rail)
            obs.DEGRADED.inc()
            streaming, degraded = {n: math.nan for n in rf.rail_feature_names(self.rail)}, True
        feats.update(streaming)
        feats.update(rf.derived_features(payment, feats))
        return feats, degraded

    def extra(self, payment, feats: dict) -> tuple[float | None, list[ReasonCode], list[str], Decision]:
        """Hook for rail-specific intelligence (crypto). Returns (external risk, reasons, actions, decision)."""
        return None, [], [], Decision.APPROVE

    def assess(self, payment) -> Assessment:
        feats, degraded = self.features(payment)
        ext_p, ext_reasons, actions, ext_decision = self.extra(payment, feats)
        p_card, z, contrib = self.card.score(feats)
        # Independent risk sources combine by noisy-OR.
        p = p_card if ext_p is None else 1 - (1 - p_card) * (1 - ext_p)
        rule_decision, hits = self.rules.evaluate(feats)
        decision = most_severe(self.card.decide(p_card), rule_decision, ext_decision)
        reasons = [ReasonCode(code="scorecard_risk", detail="Elevated risk", weight=round(p_card, 4))] \
            if self.card.decide(p_card) != Decision.APPROVE else []
        reasons += ext_reasons
        reasons += [ReasonCode(code=f"rule:{r.id}", detail=r.description) for r in hits if r.mode == "enforce"]
        if degraded:
            reasons.append(ReasonCode(code="degraded", detail="Behavioural history unavailable"))
        for r in hits:
            obs.RULE_HITS.labels(r.id, r.mode).inc()
        decision, actions = self.finalize(payment, decision, actions)
        return Assessment(
            feats, p, z, self.card.version, decision, reasons,
            [r.id if r.mode == "enforce" else f"shadow:{r.id}" for r in hits],
            {"bin_key": None, "customer_key": _hash(f"{self.rail}:{payment.account_id}"),
             "device_key": _device_key(payment.device), "counterparty_key": _hash(self.counterparty(payment))},
            p * payment.usd, actions=actions, degraded=degraded,
            explanation=contrib + ([{"feature": "external_risk", "detail": r.detail, "weight": r.weight}
                                    for r in ext_reasons]))

    def counterparty(self, payment) -> str | None:
        return {"bank_transfer": lambda: payment.beneficiary_account,
                "mobile_money": lambda: payment.counterparty_wallet,
                "crypto": lambda: payment.counterparty_address}[self.rail]()

    def finalize(self, payment, decision: Decision, actions: list[str]) -> tuple[Decision, list[str]]:
        if decision == Decision.REVIEW and self.rail in ("bank_transfer", "mobile_money"):
            actions = actions + ["hold_payment"]  # push payments are irrevocable once sent: hold, then review
        return decision, actions


class CryptoScorer(ScorecardScorer):
    """Crypto adds three intelligence sources to the behavioural scorecard:
       1. OFAC SDN screening (hard control)  2. counterparty risk from the address intelligence store
       3. FATF Travel Rule completeness (rule on features computed in features/rails.py)."""

    def __init__(self, store, config_dir: Path, screener, intel=None, combiner=None, intel_version: str | None = None,
                 travel_rule_threshold_usd: float = 1000.0):
        super().__init__("crypto", store, config_dir, travel_rule_threshold_usd)
        self.screener, self.intel, self.combiner, self.intel_version = screener, intel, combiner, intel_version

    def extra(self, payment, feats):
        reasons, actions, decision, ext_p = [], [], Decision.APPROVE, None
        hit = self.screener.screen(payment.counterparty_address)
        feats["sanctions_hit"] = float(hit.hit)
        if hit.hit:
            reasons.append(ReasonCode(code="sanctions:ofac_sdn", weight=1.0,
                                      detail=f"Counterparty address on OFAC SDN list ({', '.join(hit.assets)})"))
            actions.append("file_sanctions_report")
            decision, ext_p = Decision.DECLINE, 1.0
        if self.intel is not None and self.combiner is not None and payment.chain.lower() == "bitcoin":
            tx_id = int(payment.tx_hash) if payment.tx_hash and payment.tx_hash.isdigit() else None
            prof = self.intel.profile(payment.counterparty_address, step_of(payment.event_time), tx_id)
            cp = self.combiner.score(prof)
            feats.update({f"cp_{k}": v for k, v in prof.items()})
            feats["counterparty_risk"] = cp
            ext_p = max(ext_p or 0.0, cp)
            if cp >= self.combiner.p["block_threshold"]:
                decision = most_severe(decision, Decision.DECLINE)
                reasons.append(ReasonCode(code="counterparty:high_risk", weight=round(cp, 4),
                                          detail="Counterparty address linked to illicit activity"))
            elif cp >= self.combiner.p["review_threshold"]:
                decision = most_severe(decision, Decision.REVIEW)
                reasons.append(ReasonCode(code="counterparty:elevated_risk", weight=round(cp, 4),
                                          detail="Counterparty address shows elevated illicit exposure"))
        else:
            feats["counterparty_risk"] = math.nan  # no on-chain intelligence for this chain yet
        return ext_p, reasons, actions, decision

    def finalize(self, payment, decision, actions):
        if payment.direction == "deposit":
            # Funds are already on-chain and cannot be refused: a "decline" becomes freeze + review.
            if decision == Decision.DECLINE:
                return Decision.REVIEW, actions + ["freeze_funds"]
            if decision == Decision.REVIEW:
                return decision, actions + ["hold_credit"]
            return decision, actions
        if decision == Decision.DECLINE:
            return decision, actions + ["block_withdrawal"]
        if decision == Decision.REVIEW:
            return decision, actions + ["hold_withdrawal"]
        return decision, actions

    @property
    def version(self) -> str:
        return f"{self.card.version}+{self.intel_version or 'no-intel'}"

    def assess(self, payment) -> Assessment:
        a = super().assess(payment)
        a.model_version = self.version
        return a
