"""The single feature definition used by BOTH offline backfill (training data) and online scoring.

    features = request_features(txn) | signals(txn) | streaming_features(txn, store)

Entities tracked in the online store (named for what the IEEE-CIS fields actually identify):
  bin        card profile (card1..card6: issuer/BIN-level attributes). SHARED by many customers, so its
             velocity is BIN-level activity, not one card's.
  customer   card profile + billing region + payer email: the closest proxy to one card holder.
  device     DeviceInfo + OS + browser + screen: a coarse fingerprint, not a physical device ID, so
             "distinct customers per device" is inflated for popular configurations.
"""

from __future__ import annotations

import hashlib
import math
import re

from payguard.features import state as st
from payguard.features.store import FeatureStore
from payguard.schemas import TransactionIn

ENTITIES = ("bin", "customer", "device")
# For each entity, which related key's distinct count is informative.
RELATED = {"bin": "addr", "customer": "device", "device": "customer"}

STREAMING_FEATURES = [f"{e}_{n}" for e in ENTITIES for n in st.read_features(None, 0.0, 1.0)] + [
    f"{e}_n_distinct_{RELATED[e]}" for e in ENTITIES]

CATEGORICAL = ["product_code", "card_network", "card_type", "payer_email_domain", "recipient_email_domain",
               "device_type", "device_info", "device_os", "device_browser"] + [f"M{i}" for i in range(1, 10)]
SIGNAL_CATEGORICAL = {f"M{i}" for i in range(1, 10)}


def _h(s: str) -> str:
    return hashlib.blake2b(s.encode(), digest_size=8).hexdigest()


def entity_keys(txn: TransactionIn) -> dict[str, str | None]:
    c, d = txn.card, txn.device
    profile = "|".join(x or "" for x in (c.bin, c.issuer, c.country_code, c.category_code, c.network, c.type))
    bin_key = _h(profile) if c.bin else None
    customer = _h(f"{profile}|{txn.billing.region or ''}|{txn.payer_email_domain or ''}") if c.bin else None
    dev_parts = (d.info, d.os, d.browser, d.screen)
    device = _h("|".join(x or "" for x in dev_parts)) if sum(x is not None for x in dev_parts) >= 2 else None
    return {"bin": bin_key, "customer": customer, "device": device}


def _related_values(txn: TransactionIn, keys: dict[str, str | None]) -> dict[str, str | None]:
    return {"addr": txn.billing.region, "device": keys["device"], "customer": keys["customer"]}


def request_features(txn: TransactionIn) -> dict[str, float | str | None]:
    """Stateless features computable from the request alone."""
    t = txn.event_time
    cents = round(txn.amount - math.floor(txn.amount), 3)
    p, r = txn.payer_email_domain, txn.recipient_email_domain
    return {
        "amount": txn.amount,
        "log_amount": math.log1p(txn.amount),
        "amount_cents": cents,
        "amount_is_round": 1.0 if cents == 0 else 0.0,
        "hour": float(t.hour),
        "dow": float(t.weekday()),
        "card_bin": _num(txn.card.bin),
        "card_issuer": _num(txn.card.issuer),
        "card_country": _num(txn.card.country_code),
        "card_category": _num(txn.card.category_code),
        "billing_region": _num(txn.billing.region),
        "billing_country": _num(txn.billing.country),
        "distance": txn.distance,
        "distance_secondary": txn.distance_secondary,
        "email_match": None if not p or not r else float(p == r),
        "has_identity": 0.0 if txn.device.type is None else 1.0,
        "product_code": txn.product_code,
        "card_network": txn.card.network,
        "card_type": txn.card.type,
        "payer_email_domain": p,
        "recipient_email_domain": r,
        "device_type": txn.device.type,
        "device_info": txn.device.info,
        # Families, not versions: "chrome 65.0" -> "chrome", "iOS 11.2.1" -> "ios 11". Version strings churn
        # with every release, so raw values drift monthly and unseen versions encode as missing.
        "device_os": _os_family(txn.device.os),
        "device_browser": _browser_family(txn.device.browser),
    }


def _browser_family(v: str | None) -> str | None:
    if not v:
        return None
    # The source changed format mid-dataset: April reports "mobile safari generic" / "chrome generic for
    # android", May reports "mobile safari 11.0" (caught by the drift monitor). Keep only the family.
    fam = re.split(r"[\d/]", v.lower(), maxsplit=1)[0]
    fam = re.sub(r"\bfor\b.*$|\bgeneric\b", "", fam)
    fam = re.sub(r"\s+", " ", fam).strip(" .-_")
    return fam or None


def _os_family(v: str | None) -> str | None:
    if not v:
        return None
    m = re.match(r"([a-zA-Z ]+?)\s*(\d+)?(?:[._]\d+)*\s*$", v.strip())
    if not m:
        return v.lower()
    fam, major = m.group(1).strip().lower(), m.group(2)
    return f"{fam} {major}" if major else fam


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def signal_features(txn: TransactionIn) -> dict[str, float | str | None]:
    return {f"sig_{k}" if k not in SIGNAL_CATEGORICAL else k: v for k, v in txn.signals.items()}


class FeaturePipeline:
    def __init__(self, store: FeatureStore):
        self.store = store

    def streaming_features(self, txn: TransactionIn, dedupe: bool = True) -> tuple[dict[str, float], bool]:
        keys = entity_keys(txn)
        related = _related_values(txn, keys)
        store_keys = [f"{e}:{k}" for e, k in keys.items() if k is not None]
        ts, amount = txn.ts, txn.amount

        def compute(states: dict[str, dict | None]):
            feats: dict[str, float] = {}
            new_states: dict[str, dict] = {}
            for ent in ENTITIES:
                k = keys[ent]
                sk = f"{ent}:{k}" if k is not None else None
                cur = states.get(sk) if sk else None
                for name, val in st.read_features(cur, ts, amount).items():
                    feats[f"{ent}_{name}"] = val if k is not None else float("nan")
                rel_kind = RELATED[ent]
                feats[f"{ent}_n_distinct_{rel_kind}"] = (
                    st.distinct_recent(cur, rel_kind, ts) if k is not None else float("nan"))
                if sk:
                    new_states[sk] = st.fold_event(cur or st.new_state(), ts, amount, {rel_kind: related[rel_kind]})
            return feats, new_states

        return self.store.transact(txn.transaction_id if dedupe else None, store_keys, compute)

    def build(self, txn: TransactionIn, dedupe: bool = True) -> tuple[dict, bool]:
        streaming, replayed = self.streaming_features(txn, dedupe=dedupe)
        return {**request_features(txn), **signal_features(txn), **streaming}, replayed

    @staticmethod
    def build_degraded(txn: TransactionIn) -> dict:
        """Features without the online store: streaming features missing (the model handles NaN)."""
        nan = float("nan")
        return {**request_features(txn), **signal_features(txn), **{f: nan for f in STREAMING_FEATURES}}
