"""Abstract base class and shared types for all trading scenarios."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

# ── Signal schema ─────────────────────────────────────────────────────────────
# generate_signals() must return a DataFrame with at least these columns.
SIGNAL_SCHEMA: dict[str, type] = {
    "symbol": str,
    "date": date,
    "action": str,  # 'BUY' | '' (empty = no signal)
    "scenario_id": str,
}

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "scenarios"


# ── Position ──────────────────────────────────────────────────────────────────


@dataclass
class Position:
    """Represents an open position passed to get_exit_signal()."""

    symbol: str
    scenario_id: str
    entry_date: date
    entry_price: float
    quantity: int
    market: str = "US"  # 'JP' | 'US'
    peak_price: float = 0.0  # highest close since entry (trailing stop basis)
    holding_days: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price


# ── Exit reason ───────────────────────────────────────────────────────────────


class ExitReason:
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TREND_REVERSAL = "trend_reversal"
    TAKE_PROFIT = "take_profit"
    TIME_EXIT = "time_exit"
    PRE_EARNINGS = "pre_earnings"
    NO_EXIT = ""


# ── Parameter base model ──────────────────────────────────────────────────────


class ScenarioParams(BaseModel):
    """Base Pydantic model for scenario parameters loaded from YAML."""

    model_config = ConfigDict(extra="allow")

    scenario_id: str
    name: str
    enabled: bool = True
    version: str = "1.0.0"


# ── Abstract base ─────────────────────────────────────────────────────────────


class ScenarioBase(ABC):
    """Abstract base for all trading scenarios.

    Subclasses must:
    1. Define a Pydantic params class (subclass of ScenarioParams)
    2. Implement generate_signals() and get_exit_signal()
    3. Load parameters from config/scenarios/<id>.yaml in __init__

    Core contract:
    - generate_signals(data) uses ONLY columns present in data at time t.
      No future data may be referenced. All indicators must be pre-computed.
    - get_exit_signal() is a pure function of the position and current row.
    - Both methods are side-effect free (pure functions).
    """

    scenario_id: str  # Must be set by subclass (e.g. "S2")

    def __init__(self, config_path: Path | str | None = None) -> None:
        if config_path is None:
            config_path = _CONFIG_DIR / f"{self.scenario_id.lower()}.yaml"
        self._raw_params = _load_yaml(Path(config_path))
        self.params = self._parse_params(self._raw_params)

    @abstractmethod
    def _parse_params(self, raw: dict[str, Any]) -> ScenarioParams:
        """Parse raw YAML dict into typed Pydantic params model."""

    @abstractmethod
    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        """Generate BUY signals from indicator-enriched OHLCV data.

        Args:
            data: Polars DataFrame for a single symbol, sorted by date ascending.
                  Must contain pre-computed indicators (ma_20, rsi_14, etc.).
                  Only data up to and including the current date is passed —
                  no future rows are present.

        Returns:
            DataFrame with columns: symbol, date, action, scenario_id.
            action is 'BUY' when entry conditions are met, '' otherwise.
            One row per trading day.

        Lookahead rule: a BUY signal on date t means the strategy would
        enter at the NEXT trading day's open. Computation on row t must
        use only columns from row t or earlier.
        """

    @abstractmethod
    def get_exit_signal(self, position: Position, current_data: dict[str, Any]) -> str:
        """Determine exit reason for an open position given today's data.

        Args:
            position: The open position with entry price, peak price, etc.
            current_data: A dict mapping column name → scalar value for
                          today's OHLCV + indicators (e.g. from df.row(i, named=True)).

        Returns:
            ExitReason constant if position should be closed today,
            ExitReason.NO_EXIT ('') otherwise.
        """

    @property
    def is_enabled(self) -> bool:
        return self.params.enabled

    def is_enabled_for_market(self, market: str) -> bool:
        """Return True if this scenario should run for the given market.

        If ``enabled_markets`` is absent from the YAML, the scenario runs for
        all markets (backward-compatible default).  When present it must be a
        list of market codes, e.g. ``[US]``.
        """
        if not self.is_enabled:
            return False
        markets: list[str] | None = getattr(self, "_raw_params", {}).get("enabled_markets")
        if markets is None:
            return True
        return market.upper() in [m.upper() for m in markets]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.scenario_id}, v={self.params.version})"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Scenario config not found: {path}")
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return data


def build_signal_row(
    symbol: str,
    signal_date: date,
    action: str,
    scenario_id: str,
    **metadata: Any,
) -> dict[str, Any]:
    """Build a single signal row dict compatible with SIGNAL_SCHEMA."""
    return {
        "symbol": symbol,
        "date": signal_date,
        "action": action,
        "scenario_id": scenario_id,
        **metadata,
    }
