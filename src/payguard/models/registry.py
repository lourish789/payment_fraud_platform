"""File-based model registry and the loaded model bundle used for serving.

Layout:  artifacts/models/<version>/{model.txt, encoder.json, calibration.json, policy.json,
                                     metadata.json, reference.json}
         artifacts/models/registry.json  -> {"champion": v, "challenger": v|null, "history": [...]}

Versions are immutable; promotion is a pointer swap recorded in history (who/when/why), so a bad
release is rolled back by promoting the previous version. In a larger team this maps 1:1 onto the
MLflow Model Registry's champion/challenger aliases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np

from payguard.models.encoder import FeatureEncoder
from payguard.models.policy import Policy


@dataclass
class ModelBundle:
    version: str
    booster: lgb.Booster
    encoder: FeatureEncoder
    calib_x: np.ndarray
    calib_y: np.ndarray
    policy: Policy
    metadata: dict
    reference: dict

    @classmethod
    def load(cls, path: Path) -> "ModelBundle":
        calib = json.loads((path / "calibration.json").read_text())
        return cls(
            version=path.name,
            booster=lgb.Booster(model_file=str(path / "model.txt")),
            encoder=FeatureEncoder.load(path / "encoder.json"),
            calib_x=np.asarray(calib["x"]), calib_y=np.asarray(calib["y"]),
            policy=Policy(**json.loads((path / "policy.json").read_text())),
            metadata=json.loads((path / "metadata.json").read_text()),
            reference=json.loads((path / "reference.json").read_text()),
        )

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        return np.interp(raw, self.calib_x, self.calib_y)

    def predict_row(self, feats: dict) -> tuple[np.ndarray, float, float]:
        x = self.encoder.transform_row(feats)
        raw = float(self.booster.predict(x, num_threads=1)[0])
        return x, raw, float(self.calibrate(np.array([raw]))[0])

    def contributions(self, x: np.ndarray, top: int = 5) -> list[tuple[str, float]]:
        """Per-feature log-odds contributions (TreeSHAP, computed by LightGBM natively)."""
        contrib = self.booster.predict(x, pred_contrib=True, num_threads=1)[0][:-1]  # last = bias
        idx = np.argsort(-contrib)[:top]
        return [(self.encoder.columns[i], float(contrib[i])) for i in idx if contrib[i] > 0]


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.file = root / "registry.json"

    def read(self) -> dict:
        if not self.file.exists():
            return {"champion": None, "challenger": None, "history": []}
        return json.loads(self.file.read_text())

    def _write(self, reg: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=2))
        tmp.replace(self.file)  # atomic swap: readers never see a half-written registry

    def set_alias(self, alias: str, version: str | None, reason: str = "") -> None:
        if version is not None and not (self.root / version / "model.txt").exists():
            raise FileNotFoundError(f"unknown model version {version}")
        reg = self.read()
        reg["history"].append({"alias": alias, "from": reg.get(alias), "to": version, "reason": reason,
                               "at": datetime.now(timezone.utc).isoformat()})
        reg[alias] = version
        self._write(reg)

    def load(self, alias: str = "champion") -> ModelBundle | None:
        version = self.read().get(alias)
        return ModelBundle.load(self.root / version) if version else None

    def versions(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if (p / "model.txt").exists()) if self.root.exists() else []
