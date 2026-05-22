"""S2 – 52-week high breakout scenario."""

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

_REQUIRED_COLS = {"close", "high_252d", "vol_ratio_20", "ma_200", "ma_200_slope"}


class S2Params(ScenarioParams):
    """Parameters for S2 – 52-week high breakout."""

    high_lookback_days: int = 252
    volume_multiplier: float = 1.5
    trend_ma_days: int = 200
    trend_slope_window: int = 20
    stop_loss_pct: float = -0.08
    trailing_stop_pct: float = -0.15
    trend_exit_ma_days: int = 20
    time_exit_days: int = 180


class S2Breakout(ScenarioBase):
    """52-week high breakout with volume and trend filters.

    Entry (all conditions on the same bar):
    1. close > high_252d.shift(1)  — breaks above prior-day 252d high
    2. vol_ratio_20 >= volume_multiplier
    3. ma_200_slope > 0
    4. close > ma_200

    Exit (first matching reason wins):
    1. STOP_LOSS     : close <= entry_price * (1 + stop_loss_pct)
    2. TRAILING_STOP : close <= peak_price  * (1 + trailing_stop_pct)
    3. TREND_REVERSAL: close < ma_20  AND  ma_20_slope < 0
    4. TIME_EXIT     : holding_days >= time_exit_days
    """

    scenario_id = "S2"

    def _parse_params(self, raw: dict[str, Any]) -> S2Params:
        flat = {**raw, **raw.get("parameters", {})}
        flat.pop("parameters", None)
        flat.pop("change_log", None)
        return S2Params(**flat)

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

        p = cast(S2Params, self.params)

        signal = (
            (pl.col("close") > pl.col("high_252d").shift(1))
            & (pl.col("vol_ratio_20") >= p.volume_multiplier)
            & (pl.col("ma_200_slope") > 0)
            & (pl.col("close") > pl.col("ma_200"))
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
        p = cast(S2Params, self.params)

        def _f(col: str) -> float:
            try:
                return float(current_data[col])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                return float("nan")

        close = _f("close")

        if close <= position.entry_price * (1 + p.stop_loss_pct):
            return ExitReason.STOP_LOSS

        if close <= position.peak_price * (1 + p.trailing_stop_pct):
            return ExitReason.TRAILING_STOP

        ma_20 = _f("ma_20")
        ma_20_slope = _f("ma_20_slope")
        if not (math.isnan(ma_20) or math.isnan(ma_20_slope)):
            if close < ma_20 and ma_20_slope < 0:
                return ExitReason.TREND_REVERSAL

        if position.holding_days >= p.time_exit_days:
            return ExitReason.TIME_EXIT

        return ExitReason.NO_EXIT
