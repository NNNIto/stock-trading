"""S4 – Post-Earnings Announcement Drift (PEAD) scenario."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import polars as pl

from src.scenarios.base import (
    ExitReason,
    Position,
    ScenarioBase,
    ScenarioParams,
)

_REQUIRED_COLS = {
    "close",
    "open",
    "is_earnings_day",
    "vol_ratio_20",
    "ma_200",
    "ma_200_slope",
}


class S4Params(ScenarioParams):
    """Parameters for S4 – Post-Earnings Announcement Drift."""

    gap_up_pct: float = 0.02
    earnings_day_return_pct: float = 0.05
    volume_multiplier: float = 3.0
    surprise_threshold_pct: float = 0.0
    trend_ma_days: int = 200
    trend_slope_window: int = 20
    entry_delay_days: int = 2
    time_exit_days: int = 60
    stop_loss_pct: float = -0.10
    take_profit_pct: float = 0.25
    trailing_stop_pct: float = -0.15
    pre_earnings_exit_days: int = 5
    use_eps_filter: bool = True


class S4PEAD(ScenarioBase):
    """Post-Earnings Announcement Drift with gap-up and volume filters.

    Entry (conditions at earnings day t-n, entry day t):
    Conditions at earnings day (shifted by entry_delay_days):
    1. is_earnings_day == True
    2. gap_up >= gap_up_pct
    3. day_return >= earnings_day_return_pct
    4. vol_ratio_20 >= volume_multiplier
    5. EPS filter (use_eps_filter=True): surprise > threshold if data available;
       if null, pass through (gap+volume fallback per 4.5)

    Conditions at entry day:
    6. close > ma_200
    7. ma_200_slope > 0

    Exit (first matching reason wins):
    1. STOP_LOSS     : close <= entry_price * (1 + stop_loss_pct)
    2. TAKE_PROFIT   : close >= entry_price * (1 + take_profit_pct)
    3. TRAILING_STOP : close <= peak_price * (1 + trailing_stop_pct)
    4. PRE_EARNINGS  : days until next_report_date <= pre_earnings_exit_days
    5. TIME_EXIT     : holding_days >= time_exit_days
    """

    scenario_id = "S4"

    def _parse_params(self, raw: dict[str, Any]) -> S4Params:
        flat = {**raw, **raw.get("parameters", {})}
        flat.pop("parameters", None)
        flat.pop("change_log", None)
        return S4Params(**flat)

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        if "is_earnings_day" not in data.columns:
            return pl.DataFrame(
                {
                    "symbol": data["symbol"] if "symbol" in data.columns else [""] * data.height,
                    "date": data["date"],
                    "action": [""] * data.height,
                    "scenario_id": [self.scenario_id] * data.height,
                }
            )

        missing = (_REQUIRED_COLS - {"is_earnings_day"}) - set(data.columns)
        if missing:
            return pl.DataFrame(
                {
                    "symbol": data["symbol"] if "symbol" in data.columns else [""] * data.height,
                    "date": data["date"],
                    "action": [""] * data.height,
                    "scenario_id": [self.scenario_id] * data.height,
                }
            )

        p = cast(S4Params, self.params)
        n = p.entry_delay_days

        gap_up = (pl.col("open") - pl.col("close").shift(1)) / pl.col("close").shift(1)
        day_return = (pl.col("close") - pl.col("close").shift(1)) / pl.col("close").shift(1)

        cond_earnings = pl.col("is_earnings_day").cast(pl.Boolean).shift(n)
        cond_gap_up = gap_up.shift(n) >= p.gap_up_pct
        cond_day_return = day_return.shift(n) >= p.earnings_day_return_pct
        cond_volume = pl.col("vol_ratio_20").shift(n) >= p.volume_multiplier

        if p.use_eps_filter and "eps_surprise_pct" in data.columns:
            has_eps = pl.col("eps_surprise_pct").shift(n).is_not_null()
            eps_ok = ~has_eps | (pl.col("eps_surprise_pct").shift(n) > p.surprise_threshold_pct)
        else:
            eps_ok = pl.lit(True)

        signal = (
            cond_earnings
            & cond_gap_up
            & cond_day_return
            & cond_volume
            & eps_ok
            & (pl.col("close") > pl.col("ma_200"))
            & (pl.col("ma_200_slope") > 0)
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
        p = cast(S4Params, self.params)

        def _f(col: str) -> float:
            try:
                return float(current_data[col])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                return float("nan")

        close = _f("close")

        if close <= position.entry_price * (1 + p.stop_loss_pct):
            return ExitReason.STOP_LOSS

        if close >= position.entry_price * (1 + p.take_profit_pct):
            return ExitReason.TAKE_PROFIT

        if close <= position.peak_price * (1 + p.trailing_stop_pct):
            return ExitReason.TRAILING_STOP

        next_report = position.metadata.get("next_report_date")
        if next_report is not None:
            try:
                if isinstance(next_report, str):
                    next_report = date.fromisoformat(next_report)
                current_dt = current_data.get("date")
                if isinstance(current_dt, str):
                    current_dt = date.fromisoformat(current_dt)
                if isinstance(current_dt, date):
                    days_until = (next_report - current_dt).days
                    if days_until <= p.pre_earnings_exit_days:
                        return ExitReason.PRE_EARNINGS
            except (ValueError, TypeError, AttributeError):
                pass

        if position.holding_days >= p.time_exit_days:
            return ExitReason.TIME_EXIT

        return ExitReason.NO_EXIT
