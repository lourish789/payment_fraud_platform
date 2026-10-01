"""Turn model contributions and feature values into analyst-readable reason codes."""

from __future__ import annotations

import math

_TEMPLATES = {
    "cnt_1h": "{ent} activity in the last hour: {v:.1f} prior transactions (decayed)",
    "cnt_24h": "{ent} activity in the last 24h: {v:.1f} prior transactions (decayed)",
    "cnt_7d": "{ent} activity in the last 7d: {v:.1f} prior transactions (decayed)",
    "amt_24h": "{ent} spend in the last 24h: ${v:,.0f}",
    "amt_7d": "{ent} spend in the last 7d: ${v:,.0f}",
    "secs_since_last": "{ent} last seen {v_h} ago",
    "age_days": "{ent} first seen {v:.1f} days ago (capped at 30)",
    "amt_z": "Amount is {v:+.1f} std devs from this {ent_l}'s usual spend",
    "amt_to_mean_7d": "Amount is {v:.1f}x this {ent_l}'s 7-day average",
    "is_new": "First transaction ever seen for this {ent_l}",
    "cnt_30d": "{ent} activity in the last 30d: {v:.1f} prior transactions (decayed)",
    "n_distinct_device": "{ent} used from {v:.0f} distinct devices in 30d",
    "n_distinct_customer": "{ent} used by {v:.0f} distinct customers in 30d",
    "n_distinct_addr": "{ent} used with {v:.0f} distinct billing regions in 30d",
}
_ENTITY = {"customer": "Customer", "bin": "Card profile (BIN)", "device": "Device fingerprint"}


def _fmt_secs(s: float) -> str:
    if s < 120:
        return f"{s:.0f}s"
    if s < 7200:
        return f"{s / 60:.0f}m"
    if s < 172800:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def describe(feature: str, value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return f"{feature} is missing"
    for ent in ("customer", "bin", "device"):
        prefix = ent + "_"
        if feature.startswith(prefix) and feature[len(prefix):] in _TEMPLATES:
            v = float(value)
            return _TEMPLATES[feature[len(prefix):]].format(ent=_ENTITY[ent], ent_l=_ENTITY[ent].lower(), v=v,
                                                           v_h=_fmt_secs(v))
    if feature == "amount":
        return f"Transaction amount ${float(value):,.2f}"
    if feature.startswith("sig_"):
        return f"Processor signal {feature[4:]} = {value}"
    return f"{feature} = {value}"
