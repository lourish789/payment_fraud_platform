"""Decision policy: turn a calibrated fraud probability into approve / review / decline.

The review rule is the Bayes decision rule under a simple cost model: sending a transaction to an
analyst costs `review_cost`; approving it risks p * amount. Review when p * amount > review_cost.
Because analyst capacity is finite, `review_cost` is then raised (never lowered) until the review
rate on the validation month fits `review_capacity` - capacity is the binding constraint in practice,
and the resulting shadow price is reported.

Decline is reserved for high-confidence cases (p >= decline_threshold chosen for >= target precision
on validation), because a false decline costs a real customer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from payguard.schemas import Decision


@dataclass
class Policy:
    decline_threshold: float
    review_cost: float  # effective $ threshold on expected loss (p * amount)
    base_review_cost: float
    review_capacity: float
    decline_target_precision: float

    def decide(self, p: float, amount: float) -> Decision:
        if p >= self.decline_threshold:
            return Decision.DECLINE
        if p * amount >= self.review_cost:
            return Decision.REVIEW
        return Decision.APPROVE

    def decide_many(self, p: np.ndarray, amount: np.ndarray) -> np.ndarray:
        out = np.full(len(p), Decision.APPROVE.value, dtype=object)
        out[p * amount >= self.review_cost] = Decision.REVIEW.value
        out[p >= self.decline_threshold] = Decision.DECLINE.value
        return out

    def to_dict(self) -> dict:
        return asdict(self)


def fit_policy(p: np.ndarray, y: np.ndarray, amount: np.ndarray, review_capacity: float,
               base_review_cost: float = 5.0, decline_target_precision: float = 0.90) -> Policy:
    # Decline threshold: lowest p whose top-slice precision still meets the target.
    order = np.argsort(-p)
    prec = np.cumsum(y[order]) / np.arange(1, len(y) + 1)
    ok = np.where(prec >= decline_target_precision)[0]
    # require a minimum support so a handful of rows can't set the threshold
    ok = ok[ok >= 50]
    decline_threshold = float(p[order][ok.max()]) if len(ok) else 1.01

    not_declined = p < decline_threshold
    el = (p * amount)[not_declined]
    review_cost = base_review_cost
    max_reviews = int(review_capacity * len(p))
    if (el >= review_cost).sum() > max_reviews and max_reviews > 0:
        review_cost = float(np.sort(el)[::-1][max_reviews - 1]) + 1e-9
    return Policy(decline_threshold, review_cost, base_review_cost, review_capacity, decline_target_precision)


def policy_outcomes(decisions: np.ndarray, y: np.ndarray, amount: np.ndarray, base_review_cost: float,
                    false_decline_cost_rate: float = 0.10) -> dict:
    """Money view. Assumes analysts confirm fraud in review (a best case, stated in the README);
    a false decline costs `false_decline_cost_rate` of the amount (lost margin + churn risk)."""
    rev, dec = decisions == "review", decisions == "decline"
    stopped = rev | dec
    fraud_amt = float(amount[y == 1].sum())
    caught_amt = float(amount[(y == 1) & stopped].sum())
    review_cost = float(rev.sum() * base_review_cost)
    false_decline_cost = float(amount[(y == 0) & dec].sum() * false_decline_cost_rate)
    return {
        "n": int(len(y)),
        "review_rate": float(rev.mean()),
        "decline_rate": float(dec.mean()),
        "fraud_recall_count": float(((y == 1) & stopped).sum() / max((y == 1).sum(), 1)),
        "fraud_recall_amount": caught_amt / fraud_amt if fraud_amt else 0.0,
        "decline_precision": float(y[dec].mean()) if dec.any() else None,
        "review_precision": float(y[rev].mean()) if rev.any() else None,
        "fraud_loss_total": fraud_amt,
        "fraud_loss_prevented": caught_amt,
        "review_cost": review_cost,
        "false_decline_cost": false_decline_cost,
        "net_savings": caught_amt - review_cost - false_decline_cost,
    }
