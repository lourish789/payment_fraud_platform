"""Feature dict / frame -> model matrix. Ships inside the model artifact so serving encodes exactly
like training did (categorical vocabularies are part of the model version, not global config)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from payguard.features.pipeline import CATEGORICAL


class FeatureEncoder:
    def __init__(self, columns: list[str], vocab: dict[str, dict[str, int]]):
        self.columns = columns
        self.vocab = vocab
        self.categorical = [c for c in columns if c in vocab]
        self._index = {c: i for i, c in enumerate(columns)}

    @classmethod
    def fit(cls, df: pd.DataFrame, columns: list[str], min_count: int = 20) -> "FeatureEncoder":
        vocab = {}
        for c in columns:
            if c in CATEGORICAL:
                counts = df[c].astype("string").value_counts(dropna=True)
                values = sorted(counts[counts >= min_count].index)
                vocab[c] = {v: i for i, v in enumerate(values)}  # unseen/rare -> NaN
        return cls(columns, vocab)

    def transform_frame(self, df: pd.DataFrame) -> np.ndarray:
        out = np.empty((len(df), len(self.columns)), dtype=np.float32)
        for j, c in enumerate(self.columns):
            if c in self.vocab:
                codes = df[c].astype("string").map(self.vocab[c])
                out[:, j] = pd.to_numeric(codes, errors="coerce").to_numpy(dtype=np.float32, na_value=np.nan)
            else:
                out[:, j] = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=np.float32, na_value=np.nan)
        return out

    def transform_row(self, feats: dict) -> np.ndarray:
        row = np.full((1, len(self.columns)), np.nan, dtype=np.float32)
        for j, c in enumerate(self.columns):
            v = feats.get(c)
            if v is None:
                continue
            if c in self.vocab:
                code = self.vocab[c].get(str(v))
                if code is not None:
                    row[0, j] = code
            else:
                try:
                    row[0, j] = float(v)
                except (TypeError, ValueError):
                    pass
        return row

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"columns": self.columns, "vocab": self.vocab}))

    @classmethod
    def load(cls, path: Path) -> "FeatureEncoder":
        d = json.loads(path.read_text())
        return cls(d["columns"], d["vocab"])
