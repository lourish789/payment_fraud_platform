"""Offline evaluation of the investigation agent on replayed cases with known outcomes.

Ground truth is the dataset label, which the tools can never see for the case under investigation
(and see for other transactions only after the simulated chargeback arrived). Compared against the
model-only baseline on the same cases: "fraud if calibrated p >= 0.5". An agent is only worth its
cost if it beats that baseline, or matches it while escalating the genuinely ambiguous cases.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import numpy as np
from sqlalchemy import select

from payguard.agent.runner import Provider
from payguard.agent.tools import InvestigationTools
from payguard.db.models import Case, DecisionRecord, Label

# Claude Opus 5 list prices, $ per million tokens (input, output)
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}


def evaluate_agent(session_factory, provider: Provider, n: int = 100, seed: int = 0, out: Path | None = None,
                   explainer=None) -> dict:
    with session_factory() as s:
        rows = s.execute(select(Case.id, Label.is_fraud, DecisionRecord.fraud_probability)
                         .join(Label, Label.transaction_id == Case.transaction_id)
                         .join(DecisionRecord, DecisionRecord.transaction_id == Case.transaction_id)).all()
    rng = random.Random(seed)
    # Stratify so both classes are well represented regardless of the queue's natural mix.
    pos = [r for r in rows if r[1]]
    neg = [r for r in rows if not r[1]]
    sample = rng.sample(pos, min(n // 2, len(pos))) + rng.sample(neg, min(n - n // 2, len(neg)))
    tools = InvestigationTools(session_factory, explainer)
    per_case, tok_in, tok_out, lat, grounding = [], 0, 0, [], []
    for case_id, is_fraud, p in sample:
        t = time.perf_counter()
        res = provider.investigate(tools, tools.context(case_id))
        lat.append(time.perf_counter() - t)
        tok_in += res.input_tokens
        tok_out += res.output_tokens
        rec = res.report.recommendation if res.report else "error"
        if res.report:
            grounding.append(res.grounding)
        per_case.append({"case_id": case_id, "truth": "fraud" if is_fraud else "legit", "agent": rec,
                         "model_baseline": "fraud" if p >= 0.5 else "legit", "model_probability": round(p, 4),
                         "steps": len(res.trace), "error": res.error})

    def score(key: str) -> dict:
        decided = [c for c in per_case if c[key] in ("fraud", "legit")]
        y = np.array([c["truth"] == "fraud" for c in decided])
        yhat = np.array([c[key] == "fraud" for c in decided])
        tp = int((y & yhat).sum())
        return {
            "coverage": len(decided) / max(len(per_case), 1),
            "accuracy_on_decided": float((y == yhat).mean()) if decided else None,
            "fraud_precision": tp / max(int(yhat.sum()), 1),
            "fraud_recall": tp / max(int(y.sum()), 1),
            "escalated": sum(c[key] == "escalate" for c in per_case),
            "errors": sum(c[key] == "error" for c in per_case),
        }

    esc = [c for c in per_case if c["agent"] == "escalate"]
    decided = [c for c in per_case if c["agent"] in ("fraud", "legit")]
    model = getattr(provider, "model", None)
    price = PRICES.get(model or "", (0.0, 0.0))
    report = {
        "provider": provider.name, "model": model, "n_cases": len(per_case),
        "fraud_share_in_sample": sum(c["truth"] == "fraud" for c in per_case) / max(len(per_case), 1),
        "agent": score("agent"),
        "model_baseline": score("model_baseline"),
        # Does the agent escalate the cases the model gets wrong? (useful escalations)
        "model_accuracy_on_agent_escalations": (float(np.mean([c["model_baseline"] == c["truth"] for c in esc]))
                                                if esc else None),
        # apples-to-apples: both on exactly the cases the agent decided
        "on_agent_decided": {
            "n": len(decided),
            "agent_accuracy": float(np.mean([c["agent"] == c["truth"] for c in decided])) if decided else None,
            "model_accuracy": float(np.mean([c["model_baseline"] == c["truth"] for c in decided])) if decided else None,
        },
        "grounding": {k: float(np.mean([g[k] for g in grounding])) for k in ("citation_valid_rate", "grounded_rate")}
        if grounding else None,
        "avg_steps": float(np.mean([c["steps"] for c in per_case])),
        "latency_s": {"p50": float(np.percentile(lat, 50)), "p95": float(np.percentile(lat, 95))},
        "tokens": {"input": tok_in, "output": tok_out},
        "est_cost_usd_per_case": (tok_in * price[0] + tok_out * price[1]) / 1e6 / max(len(per_case), 1),
        "cases": per_case,
    }
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"agent_eval_{provider.name}.json").write_text(json.dumps(report, indent=2, default=str))
    return report
