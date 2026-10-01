"""Typed configuration loading and stable configuration hashing."""

from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(ValueError):
    """Raised when configuration input is invalid."""


class DataCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str | None = None
    start: date
    end: date


class UniverseCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: Literal["csi800", "hs300", "zz500"] = "csi800"
    min_listed_days: int = 60
    exclude_st: bool = True


class CostCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commission_rate: float = Field(default=0.00025, ge=0)
    commission_min: float = Field(default=5.0, ge=0)
    slippage_bp: float = Field(default=5.0, ge=0)
    impact_coef: float = Field(default=0.1, ge=0)
    cost_multiplier: float = Field(default=1.0, ge=0)


class RiskCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_weight: float = Field(default=0.10, gt=0, le=1)
    max_industry_weight: float = Field(default=0.30, gt=0, le=1)
    daily_loss_limit: float = Field(default=0.03, gt=0, le=1)
    max_drawdown_halt: float = Field(default=0.20, gt=0, le=1)
    cooldown_days: int = 5


class EngineCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_cash: float = 1_000_000
    exec_style: Literal["open_auction", "vwap", "close_auction"] = "open_auction"
    max_participation: float = 0.1


class SplitCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lockbox_start: date = date(2024, 7, 1)
    train_years: int = 3
    test_months: int = 6
    step_months: int = 6
    embargo_days: int = 5


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: DataCfg
    universe: UniverseCfg = Field(default_factory=UniverseCfg)
    costs: CostCfg = Field(default_factory=CostCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    engine: EngineCfg = Field(default_factory=EngineCfg)
    split: SplitCfg = Field(default_factory=SplitCfg)
    seed: int = 42
    strategies: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_date_order(self) -> Config:
        if self.data.start >= self.data.end:
            raise ValueError("data.start must be earlier than data.end")
        return self

    @property
    def touches_lockbox(self) -> bool:
        # lockbox_start 当天就属于锁箱期，所以是 >=（Cen 审查时修正的边界）
        return self.data.end >= self.split.lockbox_start


def _validation_message(error: ValidationError) -> str:
    details = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "config"
        details.append(f"{location}: {item['msg']}")
    return "; ".join(details)


def _merge_overrides(data: dict[str, Any], overrides: dict[str, Any]) -> None:
    for dotted_path, value in overrides.items():
        invalid_path = (
            not isinstance(dotted_path, str)
            or not dotted_path
            or any(not part for part in dotted_path.split("."))
        )
        if invalid_path:
            raise ConfigError(f"{dotted_path}: override path must be a non-empty dotted path")
        parts = dotted_path.split(".")
        cursor = data
        for part in parts[:-1]:
            existing = cursor.get(part)
            if existing is None:
                existing = {}
                cursor[part] = existing
            if not isinstance(existing, dict):
                raise ConfigError(f"{dotted_path}: cannot apply override through non-table field {part}")
            cursor = existing
        cursor[parts[-1]] = value


def load_config(path: str | Path, overrides: dict | None = None) -> Config:
    """Load TOML, apply dotted-path overrides, then validate it."""
    try:
        with Path(path).open("rb") as file:
            raw = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(str(error)) from error
    if overrides:
        _merge_overrides(raw, overrides)
    try:
        return Config.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(_validation_message(error)) from error
    except ValueError as error:
        raise ConfigError(str(error)) from error


def config_hash(cfg: Config) -> str:
    """Return the SHA256 of canonical JSON for a validated configuration."""
    payload = json.dumps(
        cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_secrets() -> dict[str, str]:
    """Load string secrets from the repository-root .env, if present."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return {}
    return {key: value for key, value in dotenv_values(env_path).items() if value is not None}
