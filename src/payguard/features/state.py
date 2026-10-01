"""Per-entity streaming state built on exponentially decayed aggregates.

Why decay instead of exact sliding windows:
  * O(1) state per entity (a sliding 7-day window needs every event in the window);
  * the update is commutative, so out-of-order events produce the same state as in-order ones
    (a late event is simply added with its already-decayed weight);
  * the same math runs in the offline backfill and the online store, which is what makes the
    training/serving parity test meaningful.

A decayed count with half-life h behaves like "events in roughly the last h", weighted toward recent.
State is a plain JSON-serialisable dict so it can live in Redis as one value per entity.

Stationarity: no feature may grow just because time passes. The data starts on 2017-12-01, so
"days since first seen", "count since first seen" and "distinct X ever seen" are left-censored and
drift upward every month by construction (the drift monitor flagged exactly this). Hence: age is capped,
the long-run count is a 30-day decayed count, and distinct counts are over a 30-day window.
"""

from __future__ import annotations

import math

HALF_LIVES_COUNT = {"1h": 3600.0, "24h": 86400.0, "7d": 7 * 86400.0}
HALF_LIVES_AMOUNT = {"24h": 86400.0, "7d": 7 * 86400.0}
HALF_LIFE_PROFILE = 30 * 86400.0  # amount mean/variance profile
DISTINCT_CAP = 32
DISTINCT_WINDOW = 30 * 86400.0
AGE_CAP_DAYS = 30.0

_LN2 = math.log(2.0)


def _decay(dt: float, half_life: float) -> float:
    return math.exp(-_LN2 * dt / half_life) if dt > 0 else 1.0


def new_state() -> dict:
    return {
        "t": None,  # max event time folded in (epoch seconds)
        "t_first": None,
        "n": 0,
        "c": {k: 0.0 for k in HALF_LIVES_COUNT},
        "a": {k: 0.0 for k in HALF_LIVES_AMOUNT},
        # decayed weight, sum and sum of squares of log-amount (for a z-score)
        "w": 0.0, "s1": 0.0, "s2": 0.0,
        "d": {},  # distinct related keys with last-seen time, e.g. {"device": {key: ts}}; 32 most recent
    }


def decayed_view(state: dict, at: float) -> dict:
    """Aggregates as of time `at` (>= state time) without mutating state."""
    t = state["t"]
    dt = 0.0 if t is None or at <= t else at - t
    return {
        "c": {k: v * _decay(dt, HALF_LIVES_COUNT[k]) for k, v in state["c"].items()},
        "a": {k: v * _decay(dt, HALF_LIVES_AMOUNT[k]) for k, v in state["a"].items()},
        "w": state["w"] * _decay(dt, HALF_LIFE_PROFILE),
        "s1": state["s1"] * _decay(dt, HALF_LIFE_PROFILE),
        "s2": state["s2"] * _decay(dt, HALF_LIFE_PROFILE),
    }


def fold_event(state: dict, ts: float, amount: float, related: dict[str, str | None]) -> dict:
    """Return a new state that includes one event. Commutative in event order."""
    s = {**state, "c": dict(state["c"]), "a": dict(state["a"]), "d": {k: dict(v) for k, v in state["d"].items()}}
    x = math.log1p(amount)
    t = s["t"]
    if t is None or ts >= t:
        dt = 0.0 if t is None else ts - t
        for k, hl in HALF_LIVES_COUNT.items():
            s["c"][k] = s["c"][k] * _decay(dt, hl) + 1.0
        for k, hl in HALF_LIVES_AMOUNT.items():
            s["a"][k] = s["a"][k] * _decay(dt, hl) + amount
        g = _decay(dt, HALF_LIFE_PROFILE)
        s["w"], s["s1"], s["s2"] = s["w"] * g + 1.0, s["s1"] * g + x, s["s2"] * g + x * x
        s["t"] = ts
    else:  # late event: add its contribution decayed to the state's clock
        lag = t - ts
        for k, hl in HALF_LIVES_COUNT.items():
            s["c"][k] += _decay(lag, hl)
        for k, hl in HALF_LIVES_AMOUNT.items():
            s["a"][k] += amount * _decay(lag, hl)
        g = _decay(lag, HALF_LIFE_PROFILE)
        s["w"] += g
        s["s1"] += x * g
        s["s2"] += x * x * g
    s["n"] += 1
    s["t_first"] = ts if s["t_first"] is None else min(s["t_first"], ts)
    for kind, value in related.items():
        if value is None:
            continue
        seen = s["d"].setdefault(kind, {})
        seen[value] = max(seen.get(value, ts), ts)
        if len(seen) > DISTINCT_CAP:  # keep the most recent: commutative, so order-independent
            del seen[min(seen, key=lambda k: (seen[k], k))]
    return s


def distinct_recent(state: dict | None, kind: str, at: float) -> float:
    if not state:
        return 0.0
    return float(sum(1 for t in state["d"].get(kind, {}).values() if at - t <= DISTINCT_WINDOW))


def read_features(state: dict | None, ts: float, amount: float) -> dict[str, float]:
    """Features describing an entity's history strictly *before* the current event."""
    nan = float("nan")
    if state is None or state["n"] == 0:
        return {"cnt_1h": 0.0, "cnt_24h": 0.0, "cnt_7d": 0.0, "cnt_30d": 0.0, "amt_24h": 0.0, "amt_7d": 0.0,
                "secs_since_last": nan, "age_days": nan, "amt_z": nan, "amt_to_mean_7d": nan, "is_new": 1.0}
    v = decayed_view(state, ts)
    mean_7d = v["a"]["7d"] / v["c"]["7d"] if v["c"]["7d"] > 1e-9 else nan
    z = nan
    if v["w"] >= 2.0:
        mu = v["s1"] / v["w"]
        var = max(v["s2"] / v["w"] - mu * mu, 1e-4)
        z = (math.log1p(amount) - mu) / math.sqrt(var)
    return {
        "cnt_1h": v["c"]["1h"], "cnt_24h": v["c"]["24h"], "cnt_7d": v["c"]["7d"], "cnt_30d": v["w"],
        "amt_24h": v["a"]["24h"], "amt_7d": v["a"]["7d"],
        "secs_since_last": max(ts - state["t"], 0.0),
        "age_days": min(max(ts - state["t_first"], 0.0) / 86400.0, AGE_CAP_DAYS),
        "amt_z": z,
        "amt_to_mean_7d": amount / mean_7d if mean_7d == mean_7d and mean_7d > 0 else nan,
        "is_new": 0.0,
    }
