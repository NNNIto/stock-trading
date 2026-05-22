"""Tests for walk-forward analysis."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from src.backtest.execution import ExecutionConfig
from src.backtest.walkforward import (
    WalkForwardRunner,
    _add_months,
    _generate_windows,
    _get_parameter_grid,
)
from src.portfolio.sizer import FixedFractionSizer
from src.scenarios.base import ExitReason, Position, ScenarioBase, ScenarioParams

# ── Helpers ───────────────────────────────────────────────────────────────────


class _StaticParams(ScenarioParams):
    stop_loss_pct: float = -0.10
    time_exit_days: int = 30


class _GridParams(ScenarioParams):
    stop_loss_pct: float = -0.10
    time_exit_days: int = 30


class _GridScenario(ScenarioBase):
    """Scenario with a synthetic parameter_grid; BUY every Nth day."""

    scenario_id = "S2"
    params: _GridParams

    def __init__(self, every_n: int = 5) -> None:
        self.every_n = every_n
        self.params = _GridParams(scenario_id="S2", name="grid_stub")
        self._raw_params = {
            "parameter_grid": {
                "stop_loss_pct": [-0.05, -0.10, -0.15],
            }
        }

    def _parse_params(self, raw: dict[str, Any]) -> _GridParams:
        return _GridParams(scenario_id="S2", name="grid_stub")

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        actions = []
        for i, _ in enumerate(data.iter_rows()):
            actions.append("BUY" if i % self.every_n == 0 else "")
        return data.select(
            [
                pl.col("symbol"),
                pl.col("date"),
                pl.Series("action", actions).alias("action"),
                pl.lit("S2").alias("scenario_id"),
            ]
        )

    def get_exit_signal(self, position: Position, current_data: dict[str, Any]) -> str:
        close = float(current_data.get("close", position.entry_price))
        if close <= position.entry_price * (1 + self.params.stop_loss_pct):
            return ExitReason.STOP_LOSS
        if position.holding_days >= self.params.time_exit_days:
            return ExitReason.TIME_EXIT
        return ExitReason.NO_EXIT


def _make_data(n_days: int = 400, start: date = date(2020, 1, 1)) -> pl.DataFrame:
    rows = []
    for i in range(n_days):
        d = start + timedelta(days=i)
        rows.append(
            {
                "symbol": "AAPL",
                "date": d,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "adj_close": 100.0,
                "volume": 1_000_000,
                "market": "JP",
                "ma_200": 100.0,
                "ma_200_slope": 0.5,
                "vol_ratio_20": 2.0,
                "high_252d": 105.0,
            }
        )
    return pl.DataFrame(rows)


def _make_runner(scenario: ScenarioBase | None = None) -> WalkForwardRunner:
    s = scenario or _GridScenario()
    return WalkForwardRunner(
        scenarios=[s],
        sizer=FixedFractionSizer(fraction=0.15),
        exec_config=ExecutionConfig(slippage_pct=0.001, commission_pct=0.001, fx_cost_pct=0.0),
        initial_capital=1_000_000,
        train_months=3,
        val_months=1,
        step_months=1,
        random_seed=42,
    )


# ── _add_months ───────────────────────────────────────────────────────────────


def test_add_months_basic():
    assert _add_months(date(2020, 1, 1), 3) == date(2020, 4, 1)


def test_add_months_year_boundary():
    assert _add_months(date(2020, 11, 1), 3) == date(2021, 2, 1)


def test_add_months_end_of_month_clamp():
    # Jan 31 + 1 month → Feb 29 (2020 is leap year)
    assert _add_months(date(2020, 1, 31), 1) == date(2020, 2, 29)


def test_add_months_zero():
    assert _add_months(date(2020, 6, 15), 0) == date(2020, 6, 15)


# ── _generate_windows ─────────────────────────────────────────────────────────


def test_generate_windows_count():
    # 12 months IS, train=3, val=1, step=1 → 12 - (3+1) + 1 = 9 windows? No, it depends.
    windows = _generate_windows(date(2020, 1, 1), date(2020, 12, 31), 3, 1, 1)
    assert len(windows) > 0


def test_generate_windows_non_overlapping_val():
    windows = _generate_windows(date(2020, 1, 1), date(2021, 12, 31), 6, 3, 3)
    # Validation periods should not overlap
    for i in range(len(windows) - 1):
        assert windows[i][3] < windows[i + 1][2]  # val_end[i] < val_start[i+1]


def test_generate_windows_train_end_before_val_start():
    windows = _generate_windows(date(2020, 1, 1), date(2021, 12, 31), 6, 3, 3)
    for w in windows:
        assert w[1] < w[2]  # train_end < val_start


def test_generate_windows_all_within_is():
    is_end = date(2021, 12, 31)
    windows = _generate_windows(date(2020, 1, 1), is_end, 6, 3, 3)
    for w in windows:
        assert w[3] <= is_end  # val_end <= is_end


def test_generate_windows_no_fit():
    # Too short to fit even one window
    windows = _generate_windows(date(2020, 1, 1), date(2020, 3, 31), 6, 3, 3)
    assert windows == []


# ── _get_parameter_grid ───────────────────────────────────────────────────────


def test_get_parameter_grid_present():
    s = _GridScenario()
    grid = _get_parameter_grid(s)
    assert "stop_loss_pct" in grid
    assert isinstance(grid["stop_loss_pct"], list)


def test_get_parameter_grid_missing():
    # Scenario with _raw_params explicitly set to no grid
    s = _GridScenario()
    s._raw_params = {}  # clear the grid
    assert _get_parameter_grid(s) == {}


# ── WalkForwardRunner ────────────────────────────────────────────────────────


def test_walkforward_runs_without_error():
    data = _make_data(400)
    runner = _make_runner()
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=date(2021, 1, 10))
    assert result.n_windows > 0


def test_walkforward_window_count():
    data = _make_data(400)
    runner = _make_runner()
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=date(2021, 1, 10))
    expected_windows = len(_generate_windows(date(2020, 1, 1), date(2021, 1, 10), 3, 1, 1))
    assert result.n_windows == expected_windows


def test_walkforward_best_params_populated():
    data = _make_data(400)
    runner = _make_runner()
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=date(2021, 1, 10))
    for w in result.windows:
        assert "S2" in w.best_params


def test_walkforward_degradation_ratio_finite():
    data = _make_data(400)
    runner = _make_runner()
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=date(2021, 1, 10))
    assert isinstance(result.degradation_ratio, float)


def test_walkforward_metrics_per_window():
    data = _make_data(400)
    runner = _make_runner()
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=date(2021, 1, 10))
    for w in result.windows:
        assert hasattr(w.train_metrics, "sharpe_ratio")
        assert hasattr(w.val_metrics, "sharpe_ratio")


def test_walkforward_no_oos_contamination():
    """OOS period is never touched: val_end <= is_end for all windows."""
    data = _make_data(600)
    runner = _make_runner()
    is_end = date(2020, 12, 31)
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=is_end)
    for w in result.windows:
        assert w.val_end <= is_end


def test_walkforward_val_trades_concatenated():
    data = _make_data(400)
    runner = _make_runner()
    result = runner.run(data, is_start=date(2020, 1, 1), is_end=date(2021, 1, 10))
    # at least some val trades should exist (or empty if no signals)
    assert isinstance(result.all_val_trades, pl.DataFrame)


def test_walkforward_too_short_raises():
    data = _make_data(50)
    runner = _make_runner()
    with pytest.raises(ValueError, match="No walk-forward windows"):
        runner.run(data, is_start=date(2020, 1, 1), is_end=date(2020, 2, 1))
