"""Runtime configuration. Every setting can be overridden with a PAYGUARD_* env var."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYGUARD_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    artifacts_dir: Path = Path("artifacts")

    # Storage backends. Unset redis_url => in-process stores (single-node dev mode).
    database_url: str = "sqlite:///./payguard.db"
    redis_url: str | None = None

    # Online feature store snapshot loaded at startup in dev mode (materialized by `payguard backfill`).
    online_snapshot: Path | None = None

    # Decision-policy inputs (capacity, review cost, decline precision) used at training time
    policy_path: Path = Path("configs/policy.yaml")
    rules_path: Path = Path("configs/rules.yaml")

    # Rate limiting defaults (per API client)
    rate_limit_rps: float = 200.0
    rate_limit_burst: int = 400

    # Background workers run inside the API process in dev mode.
    run_workers_in_process: bool = True

    # Agent
    agent_provider: str = "auto"  # auto | claude | heuristic
    agent_model: str = "claude-opus-5"
    agent_max_steps: int = 10
    agent_max_output_tokens: int = 16000
    agent_auto_investigate: bool = True

    # Vision
    receipts_dir: Path = Path("data/receipts")

    # Payment rails
    rails_enabled: list[str] = ["card", "bank_transfer", "mobile_money", "crypto"]
    rails_config_dir: Path = Path("configs/rails")
    travel_rule_threshold_usd: float = 1000.0  # FATF baseline; EU TFR is 0, US BSA 3000
    sanctions_dir: Path = Path("data/raw/crypto/ofac")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def models_dir(self) -> Path:
        return self.artifacts_dir / "models"

    @property
    def crypto_dir(self) -> Path:
        return self.models_dir / "crypto"


@lru_cache
def get_settings() -> Settings:
    return Settings()
