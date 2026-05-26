"""S3 – Pullback buy (RSI(2) + MA200 trend) scenario."""

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

_REQUIRED_COLS = {"close", "ma_200", "rsi_2"}


class S3Params(ScenarioParams):
    """Parameters for S3 – RSI(2) pullback buy within MA200 uptrend."""

    rsi_oversold: float = 10.0
    rsi_recovery: float = 50.0
    rsi_recovery_window: int = 3
    take_profit_pct: float = 0.10
    rsi_take_profit: float = 70.0
    stop_loss_pct: float = -0.07
    time_exit_days: int = 20
    trend_exit_ma_days: int = 200


class S3Pullback(ScenarioBase):
    """RSI(2) extreme-oversold bounce within a long-term uptrend (Connors-style).

    Entry (all conditions on the same bar):
    1. close > ma_200                               — long-term uptrend
    2. rsi_2 rolling_min(rsi_recovery_window) <= rsi_oversold — touched extreme oversold
    3. rsi_2 >= rsi_recovery                        — RSI(2) has snapped back
    4. close > close.shift(1)                       — price up today

    Exit (first matching reason wins):
    1. TAKE_PROFIT   : close >= entry_price*(1+take_profit_pct) OR rsi_2 >= rsi_take_profit
    2. STOP_LOSS     : close <= entry_price * (1 + stop_loss_pct)
    3. TREND_REVERSAL: close < ma_200
    4. TIME_EXIT     : holding_days >= time_exit_days
    """

    scenario_id = "S3"

    def _parse_params(self, raw: dict[str, Any]) -> S3Params:
        flat = {**raw, **raw.get("parameters", {})}
        flat.pop("parameters", None)
        flat.pop("change_log", None)
        flat.pop("trend_ma_days", None)
        flat.pop("trend_slope_window", None)
        flat.pop("rsi_period", None)
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

        rsi_rolling_min = pl.col("rsi_2").rolling_min(window_size=p.rsi_recovery_window)

        signal = (
            (pl.col("close") > pl.col("ma_200"))
            & (rsi_rolling_min <= p.rsi_oversold)
            & (pl.col("rsi_2") >= p.rsi_recovery)
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
        rsi_2 = _f("rsi_2")
        trend_ma = _f(f"ma_{p.trend_exit_ma_days}")

        price_tp = close >= position.entry_price * (1 + p.take_profit_pct)
        rsi_tp = not math.isnan(rsi_2) and rsi_2 >= p.rsi_take_profit
        if price_tp or rsi_tp:
            return ExitReason.TAKE_PROFIT

        if close <= position.entry_price * (1 + p.stop_loss_pct):
            return ExitReason.STOP_LOSS

        if not math.isnan(trend_ma) and close < trend_ma:
            return ExitReason.TREND_REVERSAL

        if position.holding_days >= p.time_exit_days:
            return ExitReason.TIME_EXIT

        return ExitReason.NO_EXIT
