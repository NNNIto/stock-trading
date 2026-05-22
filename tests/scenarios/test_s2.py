"""Tests for S2 – 52-week high breakout scenario."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from src.scenarios.base import ExitReason, Position
from src.scenarios.s2_breakout import S2Breakout, S2Params

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def s2_config(tmp_path: Path) -> Path:
    cfg: dict[str, Any] = {
        "scenario_id": "S2",
        "name": "52週高値ブレイクアウト",
        "enabled": True,
        "version": "1.0.0",
        "parameters": {
            "high_lookback_days": 252,
            "volume_multiplier": 1.5,
            "trend_ma_days": 200,
            "trend_slope_window": 20,
            "stop_loss_pct": -0.08,
            "trailing_stop_pct": -0.15,
            "trend_exit_ma_days": 20,
            "time_exit_days": 180,
        },
    }
    p = tmp_path / "s2.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def scenario(s2_config: Path) -> S2Breakout:
    return S2Breakout(config_path=s2_config)


def _make_df(overrides: dict[str, Any] | None = None, n_rows: int = 300) -> pl.DataFrame:
    """Build a 300-row DataFrame with all required indicator columns.

    The last row satisfies all S2 entry conditions by default:
    - close=110  > high_252d.shift(1)=100  (previous row high_252d)
    - vol_ratio_20=2.0 >= 1.5
    - ma_200_slope=0.5 > 0
    - close=110 > ma_200=100

    Row index n-2 sets high_252d=100 so that shift(1) on the last row == 100.
    """
    start = date(2020, 1, 2)
    dates = [start + timedelta(days=i) for i in range(n_rows)]

    base: dict[str, Any] = {
        "symbol": ["AAPL"] * n_rows,
        "date": dates,
        "close": [100.0] * n_rows,
        "open": [99.0] * n_rows,
        "high": [101.0] * n_rows,
        "low": [98.0] * n_rows,
        "adj_close": [100.0] * n_rows,
        "volume": [1_000_000] * n_rows,
        # 252d high: for rows before the last, set to 100 so shift(1) on last row == 100
        "high_252d": [100.0] * n_rows,
        "vol_ratio_20": [1.0] * n_rows,
        "vol_ma_20": [1_000_000.0] * n_rows,
        "ma_20": [95.0] * n_rows,
        "ma_50": [97.0] * n_rows,
        "ma_200": [100.0] * n_rows,
        "ma_20_slope": [0.5] * n_rows,
        "ma_50_slope": [0.3] * n_rows,
        "ma_200_slope": [0.5] * n_rows,
        "rsi_14": [50.0] * n_rows,
        "rsi_2": [30.0] * n_rows,
        "ret_5d": [0.01] * n_rows,
    }

    # Apply overrides (applied to every row unless caller handles per-row)
    if overrides:
        base.update(overrides)

    df = pl.DataFrame(base)

    # Set the last row to a clean breakout:
    #   close=110 > high_252d[n-2]=100, vol_ratio=2.0, ma_200_slope=0.5, ma_200=100
    last_close = df["close"].to_list()
    last_close[-1] = 110.0

    vol_ratio = df["vol_ratio_20"].to_list()
    vol_ratio[-1] = 2.0

    ma_200_slope = df["ma_200_slope"].to_list()
    ma_200_slope[-1] = 0.5

    ma_200 = df["ma_200"].to_list()
    ma_200[-1] = 100.0

    # high_252d of the second-to-last row = 100 (already set), last row can be anything
    high_252d = df["high_252d"].to_list()
    high_252d[-1] = 115.0  # current day's 252d high (doesn't matter for shift(1) logic)

    df = df.with_columns(
        [
            pl.Series("close", last_close),
            pl.Series("vol_ratio_20", vol_ratio),
            pl.Series("ma_200_slope", ma_200_slope),
            pl.Series("ma_200", ma_200),
            pl.Series("high_252d", high_252d),
        ]
    )

    return df


def _make_position(
    entry_price: float = 100.0,
    peak_price: float = 105.0,
    holding_days: int = 5,
) -> Position:
    return Position(
        symbol="AAPL",
        scenario_id="S2",
        entry_date=date(2024, 1, 2),
        entry_price=entry_price,
        quantity=10,
        peak_price=peak_price,
        holding_days=holding_days,
    )


def _make_row(**kwargs: Any) -> dict[str, Any]:
    """Build a row dict for get_exit_signal."""
    defaults: dict[str, Any] = {
        "close": 105.0,
        "ma_20": 100.0,
        "ma_20_slope": 0.5,
        "ma_200": 90.0,
        "rsi_2": 30.0,
    }
    defaults.update(kwargs)
    return defaults


# ── Entry signal tests ────────────────────────────────────────────────────────


def test_all_conditions_met_produces_buy(scenario: S2Breakout):
    """Full entry signal fires on the last row when all conditions are satisfied."""
    df = _make_df()
    result = scenario.generate_signals(df)
    last_action = result["action"][-1]
    assert last_action == "BUY", f"Expected BUY but got '{last_action}'"


def test_no_signal_when_close_not_above_prior_high252(scenario: S2Breakout):
    """Condition 1 failure: close does NOT exceed the prior-day 252d high."""
    df = _make_df()
    # Make the second-to-last row's high_252d = 115 so close=110 won't break out
    high_252d = df["high_252d"].to_list()
    high_252d[-2] = 115.0
    df = df.with_columns(pl.Series("high_252d", high_252d))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_no_signal_when_volume_too_low(scenario: S2Breakout):
    """Condition 2 failure: vol_ratio_20 < volume_multiplier."""
    df = _make_df()
    vol = df["vol_ratio_20"].to_list()
    vol[-1] = 1.2  # below 1.5
    df = df.with_columns(pl.Series("vol_ratio_20", vol))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_no_signal_when_ma200_slope_not_positive(scenario: S2Breakout):
    """Condition 3 failure: ma_200_slope <= 0."""
    df = _make_df()
    slope = df["ma_200_slope"].to_list()
    slope[-1] = -0.1
    df = df.with_columns(pl.Series("ma_200_slope", slope))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_no_signal_when_close_below_ma200(scenario: S2Breakout):
    """Condition 4 failure: close <= ma_200."""
    df = _make_df()
    ma200 = df["ma_200"].to_list()
    ma200[-1] = 120.0  # close=110 < ma_200=120
    df = df.with_columns(pl.Series("ma_200", ma200))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_missing_indicator_columns_returns_empty(scenario: S2Breakout):
    """When required columns are absent, all actions must be empty strings."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 10,
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(10)],
            "close": [100.0] * 10,
            # high_252d, vol_ratio_20, ma_200, ma_200_slope are missing
        }
    )
    result = scenario.generate_signals(df)
    assert all(a == "" for a in result["action"].to_list())


# ── Exit signal tests ─────────────────────────────────────────────────────────


def test_exit_stop_loss(scenario: S2Breakout):
    """STOP_LOSS triggers when close <= entry_price * (1 + stop_loss_pct)."""
    pos = _make_position(entry_price=100.0, peak_price=100.0, holding_days=1)
    # stop_loss_pct = -0.08 → threshold = 92.0
    row = _make_row(close=91.0, ma_20=95.0, ma_20_slope=0.1)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


def test_exit_trailing_stop(scenario: S2Breakout):
    """TRAILING_STOP triggers when close <= peak_price * (1 + trailing_stop_pct)."""
    pos = _make_position(entry_price=80.0, peak_price=120.0, holding_days=10)
    # trailing_stop_pct = -0.15 → threshold = 102.0
    row = _make_row(close=101.0, ma_20=110.0, ma_20_slope=0.5)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TRAILING_STOP


def test_exit_trend_reversal(scenario: S2Breakout):
    """TREND_REVERSAL triggers when close < ma_20 AND ma_20_slope < 0."""
    pos = _make_position(entry_price=100.0, peak_price=105.0, holding_days=20)
    # close=103 is safely above stop-loss (92) and trailing stop (89.25)
    row = _make_row(close=103.0, ma_20=108.0, ma_20_slope=-0.3)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TREND_REVERSAL


def test_exit_time_exit(scenario: S2Breakout):
    """TIME_EXIT triggers when holding_days >= time_exit_days (180)."""
    pos = _make_position(entry_price=100.0, peak_price=110.0, holding_days=180)
    # close is healthy — no stop-loss / trailing / trend triggers
    row = _make_row(close=108.0, ma_20=105.0, ma_20_slope=0.2)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TIME_EXIT


def test_no_exit_when_holding(scenario: S2Breakout):
    """No exit signal when all is well and holding days are low."""
    pos = _make_position(entry_price=100.0, peak_price=110.0, holding_days=5)
    row = _make_row(close=108.0, ma_20=105.0, ma_20_slope=0.5)
    assert scenario.get_exit_signal(pos, row) == ExitReason.NO_EXIT


def test_stop_loss_takes_priority_over_trend_reversal(scenario: S2Breakout):
    """STOP_LOSS has higher priority than TREND_REVERSAL."""
    pos = _make_position(entry_price=100.0, peak_price=100.0, holding_days=5)
    # close=91 triggers both stop-loss and trend reversal
    row = _make_row(close=91.0, ma_20=95.0, ma_20_slope=-0.5)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


# ── Schema ────────────────────────────────────────────────────────────────────


def test_generate_signals_schema(scenario: S2Breakout):
    """Output DataFrame must have exactly the required signal columns."""
    df = _make_df()
    result = scenario.generate_signals(df)
    for col in ("symbol", "date", "action", "scenario_id"):
        assert col in result.columns
    assert result.height == df.height
    assert all(sid == "S2" for sid in result["scenario_id"].to_list())


def test_params_loaded(scenario: S2Breakout):
    """Parameters are correctly parsed from YAML."""
    p = scenario.params
    assert isinstance(p, S2Params)
    assert p.stop_loss_pct == pytest.approx(-0.08)
    assert p.trailing_stop_pct == pytest.approx(-0.15)
    assert p.time_exit_days == 180
    assert p.volume_multiplier == pytest.approx(1.5)
