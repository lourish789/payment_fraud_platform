"""Render stored, English, human-readable text in the caller's language (payguard/i18n.py).

Decisions, explanations and reports are stored in English, exactly as they were produced: they are audit
records, and the language of whoever reads them later must not change them. Translation happens here, on
the way out, so a decision made yesterday reads in Yorùbá today. Codes, ids and numbers are left alone.
"""

from __future__ import annotations

from payguard.i18n import DEFAULT, translate


def _skip(loc: str | None) -> bool:
    return not loc or loc == DEFAULT


def reasons(items: list | None, loc: str | None) -> list | None:
    if _skip(loc) or not items:
        return items
    out = []
    for r in items:
        if isinstance(r, dict):
            out.append({**r, "detail": translate(r.get("detail"), loc)})
        else:  # ReasonCode
            out.append(r.model_copy(update={"detail": translate(r.detail, loc)}))
    return out


def decision(d: dict | None, loc: str | None) -> dict | None:
    if _skip(loc) or not d:
        return d
    return {**d, "reasons": reasons(d.get("reasons"), loc)}


def explanation(items: list[dict] | None, loc: str | None) -> list[dict] | None:
    if _skip(loc) or not items:
        return items
    return [{**e, "detail": translate(e.get("detail"), loc)} for e in items]


def investigation(inv: dict, loc: str | None) -> dict:
    report = inv.get("report")
    if _skip(loc) or not isinstance(report, dict):
        return inv
    report = {**report, "summary": translate(report.get("summary"), loc),
              "evidence": [{**e, "claim": translate(e.get("claim"), loc)} for e in report.get("evidence") or []]}
    return {**inv, "report": report}


def receipt_result(result: dict, loc: str | None) -> dict:
    if _skip(loc) or not isinstance(result, dict) or "note" not in result:
        return result
    return {**result, "note": translate(result["note"], loc)}


def case(c: dict, loc: str | None) -> dict:
    if _skip(loc) or not c:
        return c
    return {**c, "model": decision(c.get("model"), loc), "explanation": explanation(c.get("explanation"), loc),
            "investigations": [investigation(i, loc) for i in c.get("investigations") or []]}


def score(resp, loc: str | None):
    if _skip(loc):
        return resp
    return resp.model_copy(update={"reasons": reasons(resp.reasons, loc)})
