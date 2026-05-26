"""Tests for S3 – RSI(2) pullback buy (Connors-style) scenario."""

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
    "name": "押し目買い（RSI(2)＋MA200トレンド）",
    "enabled": True,
    "version": "2.0.0",
    "parameters": {
        "rsi_oversold": 10,
        "rsi_recovery": 50,
        "rsi_recovery_window": 3,
        "take_profit_pct": 0.10,
        "rsi_take_profit": 70,
        "stop_loss_pct": -0.07,
        "time_exit_days": 20,
        "trend_exit_ma_days": 200,
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
        # close is below ma_200 by default → no signal
        "close": [90.0] * n,
        "open": [89.5] * n,
        "high": [91.0] * n,
        "low": [89.0] * n,
        "ma_200": [100.0] * n,
        "rsi_2": [50.0] * n,
    }


def _make_signal_df(n: int = 300, target_row: int = 10) -> dict[str, list]:
    """All conditions met at target_row.

    rsi_recovery_window=3 means rows [target_row-2 .. target_row] form the window.
    Put rsi_2 < 10 at target_row-1, then recovery at target_row.
    """
    d = _make_base_df(n)
    r = target_row
    rw = 3  # rsi_recovery_window

    # Touch oversold within the window
    for i in range(max(0, r - rw + 1), r):
        d["rsi_2"][i] = 8.0  # below rsi_oversold=10

    # target row: all conditions met
    d["close"][r] = 105.0  # > ma_200=100
    d["close"][r - 1] = 101.0  # prev close (price bounce)
    d["ma_200"][r] = 100.0
    d["rsi_2"][r] = 55.0  # >= rsi_recovery=50

    return d


# ── Entry signal tests ────────────────────────────────────────────────────────


def test_buy_signal_all_conditions(scenario: S3Pullback) -> None:
    """All conditions met → BUY at target row."""
    d = _make_signal_df(target_row=20)
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == "BUY"


def test_no_signal_when_close_below_ma200(scenario: S3Pullback) -> None:
    """Condition 1 violated: close <= ma_200."""
    d = _make_signal_df(target_row=20)
    d["close"][20] = 99.0  # below ma_200=100
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_rsi2_never_touched_oversold(scenario: S3Pullback) -> None:
    """Condition 2 violated: rsi_2 rolling_min > rsi_oversold in the window."""
    d = _make_signal_df(target_row=20)
    for i in range(18, 20):
        d["rsi_2"][i] = 50.0  # all above oversold threshold
    d["rsi_2"][20] = 55.0
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_rsi2_not_recovered(scenario: S3Pullback) -> None:
    """Condition 3 violated: current rsi_2 < rsi_recovery=50."""
    d = _make_signal_df(target_row=20)
    d["rsi_2"][20] = 45.0  # below rsi_recovery=50
    df = pl.DataFrame(d)
    result = scenario.generate_signals(df)
    assert result["action"][20] == ""


def test_no_signal_when_no_price_bounce(scenario: S3Pullback) -> None:
    """Condition 4 violated: close[t] <= close[t-1]."""
    d = _make_signal_df(target_row=20)
    d["close"][20] = 101.0
    d["close"][19] = 101.0  # no bounce
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
        "rsi_2": 50.0,
        "ma_200": 95.0,
    }
    defaults.update(kwargs)
    return defaults


def test_exit_take_profit_price(scenario: S3Pullback) -> None:
    """TAKE_PROFIT triggered by price >= entry * 1.10."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=111.0, rsi_2=50.0, ma_200=95.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_take_profit_rsi(scenario: S3Pullback) -> None:
    """TAKE_PROFIT triggered by rsi_2 >= rsi_take_profit=70."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=105.0, rsi_2=72.0, ma_200=95.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_stop_loss(scenario: S3Pullback) -> None:
    """STOP_LOSS triggered by close <= entry * (1 - 0.07)."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=92.0, rsi_2=30.0, ma_200=95.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


def test_exit_trend_reversal_ma200(scenario: S3Pullback) -> None:
    """TREND_REVERSAL triggered by close < ma_200."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=94.0, rsi_2=45.0, ma_200=96.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TREND_REVERSAL


def test_exit_time_exit(scenario: S3Pullback) -> None:
    """TIME_EXIT triggered by holding_days >= time_exit_days=20."""
    pos = _make_position(entry_price=100.0, holding_days=20)
    row = _make_row(close=102.0, rsi_2=50.0, ma_200=95.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TIME_EXIT


def test_no_exit_when_conditions_not_met(scenario: S3Pullback) -> None:
    """NO_EXIT when no exit condition is triggered."""
    pos = _make_position(entry_price=100.0, holding_days=5)
    row = _make_row(close=105.0, rsi_2=55.0, ma_200=95.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.NO_EXIT


def test_exit_priority_take_profit_over_stop_loss(scenario: S3Pullback) -> None:
    """TAKE_PROFIT has higher priority than STOP_LOSS."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=111.0, rsi_2=50.0, ma_200=95.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


# ── Missing indicator columns → empty signals ─────────────────────────────────


def test_missing_indicators_returns_empty_signals(scenario: S3Pullback) -> None:
    """If required indicators are absent, generate_signals returns all ''."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 5,
            "date": [date(2024, 1, i + 1) for i in range(5)],
            "close": [100.0] * 5,
            # ma_200, rsi_2 are missing
        }
    )
    result = scenario.generate_signals(df)
    assert list(result["action"]) == [""] * 5
    assert list(result["scenario_id"]) == ["S3"] * 5


def test_missing_one_required_col_returns_empty_signals(scenario: S3Pullback) -> None:
    """Missing rsi_2 → empty signals."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 5,
            "date": [date(2024, 1, i + 1) for i in range(5)],
            "close": [105.0] * 5,
            "ma_200": [100.0] * 5,
            # rsi_2 missing
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
            "ma_200": [95.0] * 10,
            "rsi_2": [45.0] * 10,
        }
    )
    result = scenario.generate_signals(df)
    for col in ("symbol", "date", "action", "scenario_id"):
        assert col in result.columns, f"Missing column: {col}"
    assert result.height == 10
