"""Declarative rules engine. Rules live in YAML (configs/rules.yaml), are evaluated with a whitelist of
operators (never eval), and run alongside the model: the final decision is the most severe of the
two. Rules exist for things a model should not have to learn (hard limits, blocklists, regulatory
holds) and as the pre-ML baseline the model must beat."""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from pathlib import Path

import yaml

from payguard.schemas import Decision

OPS = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
    "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b,
}
SEVERITY = {Decision.APPROVE: 0, Decision.REVIEW: 1, Decision.DECLINE: 2}


@dataclass(frozen=True)
class Condition:
    feature: str
    op: str
    value: object

    def holds(self, feats: dict) -> bool:
        v = feats.get(self.feature)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return False  # missing data never triggers a rule
        return bool(OPS[self.op](v, self.value))


@dataclass(frozen=True)
class Rule:
    id: str
    action: Decision
    description: str
    conditions: tuple[Condition, ...]
    mode: str = "enforce"  # enforce | shadow (evaluated and logged, never changes the decision)

    def matches(self, feats: dict) -> bool:
        return all(c.holds(feats) for c in self.conditions)


class RuleEngine:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def from_yaml(cls, path: Path) -> "RuleEngine":
        doc = yaml.safe_load(path.read_text()) or {}
        return cls.from_dict(doc.get("rules", []))

    @classmethod
    def from_dict(cls, items: list[dict]) -> "RuleEngine":
        rules = []
        for r in items:
            if not r.get("enabled", True):
                continue
            conds = []
            for c in r["when"]:
                if c["op"] not in OPS:
                    raise ValueError(f"rule {r['id']}: unsupported op {c['op']}")
                conds.append(Condition(c["feature"], c["op"], c["value"]))
            mode = r.get("mode", "enforce")
            if mode not in ("enforce", "shadow"):
                raise ValueError(f"rule {r['id']}: mode must be enforce or shadow")
            rules.append(Rule(r["id"], Decision(r["action"]), r.get("description", ""), tuple(conds), mode))
        return cls(rules)

    def evaluate(self, feats: dict, include_shadow: bool = False) -> tuple[Decision, list[Rule]]:
        """Returns (most severe action among enforced hits, all hits). With include_shadow=True shadow
        rules also drive the decision - used to evaluate the rule set 'as written'."""
        hits = [r for r in self.rules if r.matches(feats)]
        acting = hits if include_shadow else [r for r in hits if r.mode == "enforce"]
        worst = max((r.action for r in acting), key=SEVERITY.get, default=Decision.APPROVE)
        return worst, hits


def most_severe(*decisions: Decision) -> Decision:
    return max(decisions, key=SEVERITY.get)
