"""Behavioural features for the non-card rails, built on the same decayed-state engine and atomic store
as card features (so idempotency, out-of-order safety and backfill/serving parity carry over).

Each rail declares the entities it tracks and, per entity, which related keys to count distinctly over
which window. The windows encode known typologies:

  bank_transfer / mobile_money (authorised push payment scams, mules)
    beneficiary <- distinct senders in 72h   fan-in: many victims paying one mule account
    sender      -> distinct payees in 30d
    pair        new payee: first transfer from this sender to this beneficiary
    agent       <- distinct cash-out senders in 24h (mobile money)
  crypto (exchange deposits and withdrawals)
    account     -> distinct external addresses in 30d
    address     <- distinct accounts in 30d   one external address collecting from many accounts
    pair        new withdrawal address for this account
    account_dep deposits only; account_wd withdrawals only -> pass-through (withdraw soon after deposit)
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from payguard.features import state as st
from payguard.features.store import FeatureStore

DAY = 86400.0


@dataclass(frozen=True)
class Related:
    kind: str
    value: Callable[[object], str | None]
    window: float


@dataclass(frozen=True)
class Entity:
    name: str
    key: Callable[[object], str | None]
    related: tuple[Related, ...] = field(default_factory=tuple)
    # State is always READ (a withdrawal must see the deposit history) but only UPDATED when this holds.
    update_when: Callable[[object], bool] | None = None


def _is(direction: str):
    return lambda p: p.direction == direction


RAIL_ENTITIES: dict[str, tuple[Entity, ...]] = {
    "bank_transfer": (
        Entity("sender", lambda p: p.account_id, (Related("payee", lambda p: p.beneficiary_account, 30 * DAY),)),
        Entity("beneficiary", lambda p: p.beneficiary_account, (Related("sender", lambda p: p.account_id, 3 * DAY),)),
        Entity("pair", lambda p: f"{p.account_id}>{p.beneficiary_account}"),
    ),
    "mobile_money": (
        Entity("sender", lambda p: p.account_id, (Related("payee", lambda p: p.counterparty_wallet, 30 * DAY),)),
        Entity("receiver", lambda p: p.counterparty_wallet, (Related("sender", lambda p: p.account_id, 3 * DAY),)),
        Entity("pair", lambda p: f"{p.account_id}>{p.counterparty_wallet}"),
        Entity("agent", lambda p: p.agent_id, (Related("sender", lambda p: p.account_id, 1 * DAY),)),
    ),
    "crypto": (
        Entity("account", lambda p: p.account_id, (Related("address", lambda p: p.counterparty_address, 30 * DAY),)),
        Entity("address", lambda p: p.counterparty_address, (Related("account", lambda p: p.account_id, 30 * DAY),)),
        Entity("pair", lambda p: f"{p.account_id}>{p.direction}>{p.counterparty_address}"),
        Entity("account_dep", lambda p: p.account_id, update_when=_is("deposit")),
        Entity("account_wd", lambda p: p.account_id, update_when=_is("withdrawal")),
    ),
}


def _h(rail: str, entity: str, key: str) -> str:
    return f"{rail}:{entity}:" + hashlib.blake2b(key.encode(), digest_size=8).hexdigest()


def rail_feature_names(rail: str) -> list[str]:
    names = []
    for e in RAIL_ENTITIES[rail]:
        names += [f"{e.name}_{n}" for n in st.read_features(None, 0.0, 1.0)]
        names += [f"{e.name}_n_distinct_{r.kind}" for r in e.related]
    return names


def rail_entity_keys(payment) -> dict[str, str | None]:
    out = {}
    for e in RAIL_ENTITIES[payment.rail]:
        k = e.key(payment)
        out[e.name] = _h(payment.rail, e.name, k) if k else None
    return out


def streaming_features(store: FeatureStore, payment, dedupe: bool = True) -> tuple[dict[str, float], bool]:
    rail, ts, amount = payment.rail, payment.ts, payment.usd
    entities = RAIL_ENTITIES[rail]
    keys = rail_entity_keys(payment)

    def compute(states: dict[str, dict | None]):
        feats: dict[str, float] = {}
        new_states: dict[str, dict] = {}
        for e in entities:
            sk = keys[e.name]
            cur = states.get(sk) if sk else None
            for name, val in st.read_features(cur, ts, amount).items():
                feats[f"{e.name}_{name}"] = val if sk else math.nan
            for r in e.related:
                feats[f"{e.name}_n_distinct_{r.kind}"] = st.distinct_recent(cur, r.kind, ts, r.window) if sk else math.nan
            if sk and (e.update_when is None or e.update_when(payment)):
                related = {r.kind: r.value(payment) for r in e.related}
                new_states[sk] = st.fold_event(cur or st.new_state(), ts, amount, related)
        return feats, new_states

    return store.transact(f"{rail}:{payment.transaction_id}" if dedupe else None,
                          [k for k in keys.values() if k], compute)


def request_features(payment, travel_rule_threshold_usd: float = 1000.0) -> dict:
    """Stateless per-rail features. Shared ones first, then rail-specific."""
    usd = payment.usd
    f: dict = {
        "amount_usd": usd,
        "log_amount_usd": math.log1p(usd),
        "hour": float(payment.event_time.hour),
        "account_age_days": payment.account_age_days,
        "new_account": None if payment.account_age_days is None else float(payment.account_age_days < 7),
        "has_device": float(payment.device.info is not None or payment.device.os is not None),
    }
    if payment.rail == "bank_transfer":
        f.update(channel=payment.channel, scheme=payment.scheme,
                 beneficiary_name_given=float(bool(payment.beneficiary_name)))
    elif payment.rail == "mobile_money":
        f.update(kind=payment.kind, sim_swap_days=payment.sim_swap_days,
                 recent_sim_swap=None if payment.sim_swap_days is None else float(payment.sim_swap_days < 2),
                 is_cash_out=float(payment.kind == "cash_out"))
    elif payment.rail == "crypto":
        tr = payment.travel_rule
        hosted = payment.counterparty_vasp is not None
        required = hosted and usd >= travel_rule_threshold_usd
        complete = bool(tr and tr.originator_name and tr.beneficiary_name
                        and (tr.originator_account or tr.beneficiary_account))
        f.update(direction=payment.direction, asset=payment.asset.upper(), chain=payment.chain.lower(),
                 counterparty_hosted=float(hosted), travel_rule_required=float(required),
                 travel_rule_missing=float(required and not complete))
    return f


def derived_features(payment, feats: dict) -> dict:
    """Cross-entity features computed after streaming features are read."""
    out = {}
    if payment.rail == "crypto" and payment.direction == "withdrawal":
        dep_24h = feats.get("account_dep_amt_24h") or 0.0
        out["passthrough_ratio_24h"] = payment.usd / dep_24h if dep_24h > 0 else 0.0
        secs = feats.get("account_dep_secs_since_last")
        out["mins_since_last_deposit"] = secs / 60.0 if secs is not None and not math.isnan(secs) else math.nan
    return out
