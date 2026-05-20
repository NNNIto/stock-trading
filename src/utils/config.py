"""Settings loader using Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ExecutionConfig(BaseModel):
    slippage_pct: float = 0.002
    commission_pct: float = 0.001
    fx_cost_pct: float = 0.005


class RiskConfig(BaseModel):
    max_position_pct: float = 0.20
    default_position_pct: float = 0.15
    max_positions: int = 7
    max_sector_concentration: int = 3
    portfolio_dd_circuit_breaker: float = -0.20


class MacroFilterConfig(BaseModel):
    vix_threshold: float = 35.0
    nikkei_drawdown_threshold: float = -0.10
    fomc_pause_days: int = 1
    boj_pause_days: int = 1


class BacktestConfig(BaseModel):
    learning_window_months: int = 12
    validation_window_months: int = 3
    walkforward_step_months: int = 3
    out_of_sample_start: str = "2025-01-01"
    random_seed: int = 42


class DataSourcesConfig(BaseModel):
    primary: str = "yfinance"
    fallback_order: list[str] = Field(default_factory=lambda: ["stooq"])
    retry_attempts: int = 3
    cross_check_tolerance_pct: float = 0.02
    cross_check_enabled: bool = True


class DataUniverseConfig(BaseModel):
    jp: str = "nikkei225"
    us: str = "sp500"


class DataConfig(BaseModel):
    start_date: str = "2018-01-01"
    universe: DataUniverseConfig = Field(default_factory=DataUniverseConfig)
    sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)


class NotificationConfig(BaseModel):
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"
    alert_on: list[str] = Field(default_factory=lambda: ["new_signal"])


class ProjectConfig(BaseModel):
    name: str = "stock-trading"
    capital_jpy: int = 3_000_000


class Settings(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    macro_filters: MacroFilterConfig = Field(default_factory=MacroFilterConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)


def load_settings(config_path: Path | str | None = None) -> Settings:
    """Load settings from YAML file."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        return Settings()

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return Settings.model_validate(raw)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
