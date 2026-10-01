"""End-to-end training run on a small synthetic dataset in the production file layout."""

import json
from datetime import timedelta

import pandas as pd
import yaml

from payguard.config import get_settings
from payguard.data.splits import split_of
from payguard.features.backfill import _schema, _to_table
from payguard.features.pipeline import FeaturePipeline
from payguard.features.store import InMemoryFeatureStore
from payguard.models.registry import Registry
from tests.helpers import REPO, synthetic_stream


def test_train_registers_a_complete_bundle(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    # spread 6,000 synthetic transactions over the Dec-May calendar so every split is populated
    pipe, rows = FeaturePipeline(InMemoryFeatureStore()), []
    start = pd.Timestamp("2017-12-01", tz="UTC").to_pydatetime()
    for i, (t, y) in enumerate(synthetic_stream(6000, seed=3)):
        t = t.model_copy(update={"event_time": start + timedelta(minutes=i * 43)})
        f, _ = pipe.build(t, dedupe=False)
        f.update(transaction_id=t.transaction_id, event_time=t.event_time, label=y)
        rows.append(f)
    split = split_of(pd.Series([r["event_time"] for r in rows]))
    for r, sp in zip(rows, split):
        r["split"] = sp
    schema = _schema([k for k in rows[0] if k not in ("transaction_id", "event_time", "label", "split")])
    pq.write_table(_to_table(rows, schema), processed / "features.parquet")
    (processed / "manifest.json").write_text(json.dumps({"source": "synthetic", "sha256": {}}))

    monkeypatch.setenv("PAYGUARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PAYGUARD_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PAYGUARD_RULES_PATH", str(REPO / "configs" / "rules.yaml"))
    monkeypatch.setenv("PAYGUARD_POLICY_PATH", str(REPO / "configs" / "policy.yaml"))
    get_settings.cache_clear()
    try:
        from payguard.models.train import train

        version = train(ablations=False)
    finally:
        get_settings.cache_clear()

    reg = Registry(tmp_path / "artifacts" / "models")
    assert reg.read()["champion"] == version
    bundle = reg.load()
    report = bundle.metadata["report"]
    assert {"test", "valid", "policy", "decisions_test", "rules_valid", "slices_test", "top_features"} <= set(report)
    assert 0.5 < report["test"]["raw"]["roc_auc"] <= 1.0
    assert set(report["rules_valid"]) == {r["id"] for r in yaml.safe_load(
        (REPO / "configs" / "rules.yaml").read_text())["rules"]}
    _, _, p = bundle.predict_row(rows[-1])
    assert 0.0 <= p <= 1.0
