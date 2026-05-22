"""S3 – Pullback buy (RSI + trend) scenario."""

from __future__ import annotations

import math
from typing import Any, cast

import polars as pl

from src.scenarios.base import (
    ExitReason,
    Position,
    ScenarioBase,
    ScenarioParams,
)

_REQUIRED_COLS = {"close", "ma_50", "ma_50_slope", "rsi_14"}


class S3Params(ScenarioParams):
    """Parameters for S3 – pullback buy (RSI + trend)."""

    trend_ma_days: int = 50
    trend_slope_window: int = 10
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_recovery: float = 40.0
    rsi_recovery_window: int = 5
    take_profit_pct: float = 0.15
    rsi_take_profit: float = 70.0
    stop_loss_pct: float = -0.07
    time_exit_days: int = 45
    trend_exit_ma_days: int = 50


class S3Pullback(ScenarioBase):
    """Pullback buy with RSI oversold recovery and uptrend filter.

    Entry (all conditions on the same bar):
    1. close > ma_50
    2. ma_50_slope > 0
    3. rsi_14 rolling_min(rsi_recovery_window) <= rsi_oversold — touched oversold recently
    4. rsi_14 >= rsi_recovery — current RSI has recovered
    5. close > close.shift(1) — price bounced

    Exit (first matching reason wins):
    1. TAKE_PROFIT   : close >= entry_price*(1+take_profit_pct) OR rsi_14>=rsi_take_profit
    2. STOP_LOSS     : close <= entry_price * (1 + stop_loss_pct)
    3. TREND_REVERSAL: close < ma_50
    4. TIME_EXIT     : holding_days >= time_exit_days
    """

    scenario_id = "S3"

    def _parse_params(self, raw: dict[str, Any]) -> S3Params:
        flat = {**raw, **raw.get("parameters", {})}
        flat.pop("parameters", None)
        flat.pop("change_log", None)
        return S3Params(**flat)

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        missing = _REQUIRED_COLS - set(data.columns)
        if missing:
            return pl.DataFrame(
                {
                    "symbol": data["symbol"] if "symbol" in data.columns else [""] * data.height,
                    "date": data["date"],
                    "action": [""] * data.height,
                    "scenario_id": [self.scenario_id] * data.height,
                }
            )

        p = cast(S3Params, self.params)

        rsi_rolling_min = pl.col("rsi_14").rolling_min(window_size=p.rsi_recovery_window)

        signal = (
            (pl.col("close") > pl.col("ma_50"))
            & (pl.col("ma_50_slope") > 0)
            & (rsi_rolling_min <= p.rsi_oversold)
            & (pl.col("rsi_14") >= p.rsi_recovery)
            & (pl.col("close") > pl.col("close").shift(1))
        )

        return data.select(
            [
                pl.col("symbol"),
                pl.col("date"),
                pl.when(signal).then(pl.lit("BUY")).otherwise(pl.lit("")).alias("action"),
                pl.lit(self.scenario_id).alias("scenario_id"),
            ]
        )

    def get_exit_signal(self, position: Position, current_data: dict[str, Any]) -> str:
        p = cast(S3Params, self.params)

        def _f(col: str) -> float:
            try:
                return float(current_data[col])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                return float("nan")

        close = _f("close")
        rsi_14 = _f("rsi_14")
        ma_50 = _f("ma_50")

        if close >= position.entry_price * (1 + p.take_profit_pct):
            return ExitReason.TAKE_PROFIT
        if not math.isnan(rsi_14) and rsi_14 >= p.rsi_take_profit:
            return ExitReason.TAKE_PROFIT

        if close <= position.entry_price * (1 + p.stop_loss_pct):
            return ExitReason.STOP_LOSS

        if not math.isnan(ma_50) and close < ma_50:
            return ExitReason.TREND_REVERSAL

        if position.holding_days >= p.time_exit_days:
            return ExitReason.TIME_EXIT

        return ExitReason.NO_EXIT
