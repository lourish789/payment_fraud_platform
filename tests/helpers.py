"""A tiny but real model bundle trained on synthetic transactions, in the production artifact format."""

from __future__ import annotations

import random
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from payguard.features.pipeline import FeaturePipeline
from payguard.features.store import InMemoryFeatureStore
from payguard.models.encoder import FeatureEncoder
from payguard.models.policy import fit_policy
from payguard.models.registry import Registry
from payguard.models.train import reference_distributions, save_bundle
from tests.conftest import make_txn

REPO = Path(__file__).resolve().parents[1]


def synthetic_stream(n: int, seed: int = 0, start_minute: float = 0.0):
    rng = random.Random(seed)
    fraud_cards = {f"9{i}" for i in range(5)}
    for i in range(n):
        card = rng.choice(list(fraud_cards)) if rng.random() < 0.08 else str(rng.randint(1000, 1400))
        amount = round(rng.lognormvariate(4, 1), 2)
        y = int(card in fraud_cards and rng.random() < 0.8 or (amount > 400 and rng.random() < 0.15))
        t = make_txn(i, minutes=start_minute + i * 2.0, amount=amount, card=card,
                     device=rng.choice(["a", "b", "c", "farm"]), transaction_id=f"s{seed}-{i}")
        yield t, y


def build_tiny_model(models_dir: Path, version: str = "lgbm-test-1", seed: int = 0) -> str:
    pipe = FeaturePipeline(InMemoryFeatureStore())
    rows = []
    for t, y in synthetic_stream(3000, seed):
        f, _ = pipe.build(t, dedupe=False)
        f["label"] = y
        rows.append(f)
    df = pd.DataFrame(rows)
    cols = [c for c in df.columns if c != "label"]
    enc = FeatureEncoder.fit(df, cols, min_count=5)
    X, y = enc.transform_frame(df), df["label"].to_numpy()
    booster = lgb.train({"objective": "binary", "verbose": -1, "num_leaves": 15, "seed": seed},
                        lgb.Dataset(X, y, feature_name=cols, categorical_feature=enc.categorical), num_boost_round=60)
    raw = booster.predict(X)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw, y)
    p = iso.predict(raw)
    policy = fit_policy(p, y, df["amount"].to_numpy(), review_capacity=0.10)
    imp = pd.Series(booster.feature_importance("gain"), index=cols).sort_values(ascending=False)
    meta = {"version": version, "report": {"top_features": [[k, float(v)] for k, v in imp.head(20).items()]}}
    save_bundle(models_dir / version, booster, enc, iso, policy, reference_distributions(df, cols, p), meta)
    reg = Registry(models_dir)
    if reg.read()["champion"] is None:
        reg.set_alias("champion", version, "test")
    return version


def fraud_like(i: int, **kw):
    """A transaction from a card that is fraudulent in the synthetic training stream."""
    return make_txn(10_000 + i, minutes=10_000 + i, amount=kw.pop("amount", 900.0), card="91", device="farm", **kw)


def model_probabilities(models_dir: Path, txns) -> np.ndarray:
    b = Registry(models_dir).load()
    pipe = FeaturePipeline(InMemoryFeatureStore())
    return np.array([b.predict_row(pipe.build(t, dedupe=False)[0])[2] for t in txns])
