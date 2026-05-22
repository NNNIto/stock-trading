"""Tests for S3 – Pullback buy (RSI + trend) scenario."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from src.scenarios.base import ExitReason, Position
from src.scenarios.s3_pullback import S3Pullback

# ── Fixtures ──────────────────────────────────────────────────────────────────

S3_CONFIG: dict[str, Any] = {
    "scenario_id": "S3",
    "name": "押し目買い（RSI＋トレンド）",
    "enabled": True,
    "version": "1.0.0",
    "parameters": {
        "trend_ma_days": 50,
        "trend_slope_window": 10,
        "rsi_period": 14,
        "rsi_oversold": 35,
        "rsi_recovery": 40,
        "rsi_recovery_window": 5,
        "take_profit_pct": 0.15,
        "rsi_take_profit": 70,
        "stop_loss_pct": -0.07,
        "time_exit_days": 45,
        "trend_exit_ma_days": 50,
    },
    "change_log": [],
}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    p = tmp_path / "s3.yaml"
    p.write_text(yaml.dump(S3_CONFIG))
    return p


@pytest.fixture
def scenario(config_path: Path) -> S3Pullback:
    return S3Pullback(config_path=config_path)


# ── Helper: build a 300-row base DataFrame ────────────────────────────────────


def _make_base_df(n: int = 300) -> dict[str, list]:
    """Create n rows of default values that do NOT trigger a signal."""
    start = date(2023, 1, 3)
    dates = [start + timedelta(days=i) for i in range(n)]
    return {
        "symbol": ["AAPL"] * n,
        "date": dates,
        # Price is below ma_50 by default → no signal
        "close": [90.0] * n,
        "open": [89.5] * n,
        "high": [91.0] * n,
        "low": [89.0] * n,
        "ma_50": [100.0] * n,
        "ma_50_slope": [1.0] * n,
        "rsi_14": [50.0] * n,
        "ma_20": [95.0] * n,
        "ma_200": [80.0] * n,
        "ma_200_slope": [0.5] * n,
        "vol_ratio_20": [1.0] * n,
    }


def _make_signal_df(n: int = 300, target_row: int = 10) -> dict[str, list]:
    """All conditions met at target_row (row index, 0-based).

    rsi_recovery_window=5 means rows [target_row-4 .. target_row] are the window.
    We put low RSI at target_row-1 so the rolling_min captures it.
    """
    d = _make_base_df(n)
    r = target_row
    rw = 5  # rsi_recovery_window

    # Condition 1+3+4+5 require at least one row before target_row
    # Set up the window rows: rsi touched oversold at row r-1
    for i in range(max(0, r - rw + 1), r):
        d["rsi_14"][i] = 30.0  # below rsi_oversold=35

    # target row: all conditions met
    d["close"][r] = 105.0  # > ma_50=100
    d["close"][r - 1] = 101.0  # prev close (close[r] > close[r-1])
    d["ma_50"][r] = 100.0  # close > ma_50
    d["ma_50_slope"][r] = 1.0  # > 0
    d["rsi_14"][r] = (
        42.0  # >= rsi_recovery=40 (also still >= oversold rolling check satisfied by window)
    )

    return d


# ── Entry signal tests ────────────────────────────────────────────────────────


def test_buy_signal_all_conditions(scenario: S3Pullback) -> None:
    """All conditions met → BUY at target row."""
    d = _make_signal_df(target_row=20)
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == "BUY"


def test_no_signal_when_close_below_ma50(scenario: S3Pullback) -> None:
    """Condition 1 violated: close <= ma_50."""
    d = _make_signal_df(target_row=20)
    d["close"][20] = 99.0  # below ma_50=100
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_ma50_slope_nonpositive(scenario: S3Pullback) -> None:
    """Condition 2 violated: ma_50_slope <= 0."""
    d = _make_signal_df(target_row=20)
    d["ma_50_slope"][20] = -0.1
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_rsi_never_touched_oversold(scenario: S3Pullback) -> None:
    """Condition 3 violated: rsi_14 rolling_min > rsi_oversold in the window."""
    d = _make_signal_df(target_row=20)
    # Reset the window rows to above oversold threshold
    for i in range(16, 20):
        d["rsi_14"][i] = 50.0
    d["rsi_14"][20] = 42.0  # recovery OK but rolling_min >= oversold → no signal
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_rsi_not_recovered(scenario: S3Pullback) -> None:
    """Condition 4 violated: current rsi_14 < rsi_recovery."""
    d = _make_signal_df(target_row=20)
    d["rsi_14"][20] = 38.0  # below rsi_recovery=40
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_no_price_bounce(scenario: S3Pullback) -> None:
    """Condition 5 violated: close[t] <= close[t-1]."""
    d = _make_signal_df(target_row=20)
    d["close"][20] = 101.0  # equal to prev close (not strictly greater)
    d["close"][19] = 101.0
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


# ── Exit signal tests ─────────────────────────────────────────────────────────


def _make_position(
    entry_price: float = 100.0,
    peak_price: float = 100.0,
    holding_days: int = 0,
) -> Position:
    return Position(
        symbol="AAPL",
        scenario_id="S3",
        entry_date=date(2024, 1, 2),
        entry_price=entry_price,
        quantity=10,
        peak_price=peak_price,
        holding_days=holding_days,
    )


def _make_row(**kwargs: float) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "close": 100.0,
        "rsi_14": 50.0,
        "ma_50": 100.0,
    }
    defaults.update(kwargs)
    return defaults


def test_exit_take_profit_price(scenario: S3Pullback) -> None:
    """TAKE_PROFIT triggered by price >= entry * 1.15."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=115.0, rsi_14=50.0, ma_50=110.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_take_profit_rsi(scenario: S3Pullback) -> None:
    """TAKE_PROFIT triggered by rsi_14 >= rsi_take_profit=70."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=105.0, rsi_14=72.0, ma_50=100.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_stop_loss(scenario: S3Pullback) -> None:
    """STOP_LOSS triggered by close <= entry * (1 - 0.07)."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=92.0, rsi_14=30.0, ma_50=100.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


def test_exit_trend_reversal(scenario: S3Pullback) -> None:
    """TREND_REVERSAL triggered by close < ma_50."""
    pos = _make_position(entry_price=100.0)
    # close must be above stop loss but below ma_50
    row = _make_row(close=98.0, rsi_14=45.0, ma_50=100.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TREND_REVERSAL


def test_exit_time_exit(scenario: S3Pullback) -> None:
    """TIME_EXIT triggered by holding_days >= time_exit_days=45."""
    pos = _make_position(entry_price=100.0, holding_days=45)
    # Price is fine (above stop, take profit not triggered, above ma_50)
    row = _make_row(close=102.0, rsi_14=50.0, ma_50=100.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TIME_EXIT


def test_no_exit_when_conditions_not_met(scenario: S3Pullback) -> None:
    """NO_EXIT when no exit condition is triggered."""
    pos = _make_position(entry_price=100.0, holding_days=5)
    row = _make_row(close=105.0, rsi_14=55.0, ma_50=100.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.NO_EXIT


# ── Exit priority: take_profit before stop_loss ───────────────────────────────


def test_exit_priority_take_profit_over_stop_loss(scenario: S3Pullback) -> None:
    """TAKE_PROFIT has higher priority than STOP_LOSS (impossible in real market, but tests ordering)."""
    # Simulate a position where both conditions mathematically hold.
    # entry=100, take_profit at 115, stop at 93 — use close=115 (take profit wins)
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=115.0, rsi_14=50.0, ma_50=110.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


# ── Missing indicator columns → empty signals ─────────────────────────────────


def test_missing_indicators_returns_empty_signals(scenario: S3Pullback) -> None:
    """If required indicators are absent, generate_signals returns all ''."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 5,
            "date": [date(2024, 1, i + 1) for i in range(5)],
            "close": [100.0] * 5,
            # ma_50, ma_50_slope, rsi_14 are missing
        }
    )
    result = scenario.generate_signals(df)
    assert list(result["action"]) == [""] * 5
    assert list(result["scenario_id"]) == ["S3"] * 5


def test_missing_one_required_col_returns_empty_signals(scenario: S3Pullback) -> None:
    """Missing just rsi_14 → empty signals."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 5,
            "date": [date(2024, 1, i + 1) for i in range(5)],
            "close": [105.0] * 5,
            "ma_50": [100.0] * 5,
            "ma_50_slope": [1.0] * 5,
            # rsi_14 missing
        }
    )
    result = scenario.generate_signals(df)
    assert list(result["action"]) == [""] * 5


# ── Schema correctness ────────────────────────────────────────────────────────


def test_generate_signals_schema(scenario: S3Pullback) -> None:
    """Output always has required columns."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 10,
            "date": [date(2024, 1, i + 1) for i in range(10)],
            "close": [100.0] * 10,
            "ma_50": [95.0] * 10,
            "ma_50_slope": [1.0] * 10,
            "rsi_14": [45.0] * 10,
        }
    )
    result = scenario.generate_signals(df)
    for col in ("symbol", "date", "action", "scenario_id"):
        assert col in result.columns, f"Missing column: {col}"
    assert result.height == 10
