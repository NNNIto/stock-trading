"""S6 – Mean-reversion (short-term bounce) scenario."""

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

_REQUIRED_COLS = {"ret_5d", "close", "ma_200", "vol_ratio_20", "rsi_2"}


class S6Params(ScenarioParams):
    """Parameters for S6 – mean-reversion (short-term bounce)."""

    return_window: int = 5
    return_threshold: float = -0.10
    trend_ma_days: int = 200
    volume_multiplier: float = 2.0
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    take_profit_pct: float = 0.05
    rsi_take_profit: float = 70.0
    stop_loss_pct: float = -0.05
    time_exit_days: int = 10


class S6Reversion(ScenarioBase):
    """Short-term mean-reversion against an oversold dip within an uptrend.

    Entry (all conditions on the same bar):
    1. ret_5d       <= return_threshold  (significant 5-day drop)
    2. close        >  ma_200            (long-term uptrend intact)
    3. vol_ratio_20 >= volume_multiplier (selling climax)
    4. rsi_2        <  rsi_oversold      (extreme short-term oversold)

    Exit (first matching reason wins):
    1. TAKE_PROFIT : close >= entry_price*(1+take_profit_pct) OR rsi_2>=rsi_take_profit
    2. STOP_LOSS   : close <= entry_price * (1 + stop_loss_pct)
    3. TIME_EXIT   : holding_days >= time_exit_days
    """

    scenario_id = "S6"

    def _parse_params(self, raw: dict[str, Any]) -> S6Params:
        flat = {**raw, **raw.get("parameters", {})}
        flat.pop("parameters", None)
        flat.pop("change_log", None)
        return S6Params(**flat)

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

        p = cast(S6Params, self.params)

        signal = (
            (pl.col("ret_5d") <= p.return_threshold)
            & (pl.col("close") > pl.col("ma_200"))
            & (pl.col("vol_ratio_20") >= p.volume_multiplier)
            & (pl.col("rsi_2") < p.rsi_oversold)
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
        p = cast(S6Params, self.params)

        def _f(col: str) -> float:
            try:
                return float(current_data[col])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                return float("nan")

        close = _f("close")
        rsi_2 = _f("rsi_2")

        price_tp = close >= position.entry_price * (1 + p.take_profit_pct)
        rsi_tp = not math.isnan(rsi_2) and rsi_2 >= p.rsi_take_profit
        if price_tp or rsi_tp:
            return ExitReason.TAKE_PROFIT

        if close <= position.entry_price * (1 + p.stop_loss_pct):
            return ExitReason.STOP_LOSS

        if position.holding_days >= p.time_exit_days:
            return ExitReason.TIME_EXIT

        return ExitReason.NO_EXIT
