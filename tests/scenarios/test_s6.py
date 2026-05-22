"""Tests for S6 – mean-reversion (short-term bounce) scenario."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from src.scenarios.base import ExitReason, Position
from src.scenarios.s6_reversion import S6Params, S6Reversion

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def s6_config(tmp_path: Path) -> Path:
    cfg: dict[str, Any] = {
        "scenario_id": "S6",
        "name": "平均回帰（短期反発）",
        "enabled": True,
        "version": "1.0.0",
        "parameters": {
            "return_window": 5,
            "return_threshold": -0.10,
            "trend_ma_days": 200,
            "volume_multiplier": 2.0,
            "rsi_period": 2,
            "rsi_oversold": 10.0,
            "take_profit_pct": 0.05,
            "rsi_take_profit": 70.0,
            "stop_loss_pct": -0.05,
            "time_exit_days": 10,
        },
    }
    p = tmp_path / "s6.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def scenario(s6_config: Path) -> S6Reversion:
    return S6Reversion(config_path=s6_config)


def _make_df(overrides: dict[str, Any] | None = None, n_rows: int = 300) -> pl.DataFrame:
    """Build a 300-row DataFrame where the last row satisfies all S6 entry conditions.

    Default last-row values:
    - ret_5d=-0.12       <= -0.10  (return_threshold)
    - close=110          >  ma_200=100
    - vol_ratio_20=2.5   >= 2.0    (volume_multiplier)
    - rsi_2=5            <  10     (rsi_oversold)
    """
    start = date(2020, 1, 2)
    dates = [start + timedelta(days=i) for i in range(n_rows)]

    base: dict[str, Any] = {
        "symbol": ["AAPL"] * n_rows,
        "date": dates,
        "close": [115.0] * n_rows,
        "open": [114.0] * n_rows,
        "high": [116.0] * n_rows,
        "low": [113.0] * n_rows,
        "adj_close": [115.0] * n_rows,
        "volume": [2_000_000] * n_rows,
        "high_252d": [120.0] * n_rows,
        "vol_ratio_20": [1.0] * n_rows,
        "vol_ma_20": [1_000_000.0] * n_rows,
        "ma_20": [112.0] * n_rows,
        "ma_50": [111.0] * n_rows,
        "ma_200": [100.0] * n_rows,
        "ma_20_slope": [0.3] * n_rows,
        "ma_50_slope": [0.2] * n_rows,
        "ma_200_slope": [0.4] * n_rows,
        "rsi_14": [45.0] * n_rows,
        "rsi_2": [30.0] * n_rows,
        "ret_5d": [0.01] * n_rows,
    }

    if overrides:
        base.update(overrides)

    df = pl.DataFrame(base)

    # Overwrite the last row to cleanly satisfy all entry conditions
    close = df["close"].to_list()
    close[-1] = 110.0
    ma_200 = df["ma_200"].to_list()
    ma_200[-1] = 100.0
    vol_ratio = df["vol_ratio_20"].to_list()
    vol_ratio[-1] = 2.5
    rsi_2 = df["rsi_2"].to_list()
    rsi_2[-1] = 5.0
    ret_5d = df["ret_5d"].to_list()
    ret_5d[-1] = -0.12

    df = df.with_columns(
        [
            pl.Series("close", close),
            pl.Series("ma_200", ma_200),
            pl.Series("vol_ratio_20", vol_ratio),
            pl.Series("rsi_2", rsi_2),
            pl.Series("ret_5d", ret_5d),
        ]
    )

    return df


def _make_position(
    entry_price: float = 100.0,
    holding_days: int = 3,
) -> Position:
    return Position(
        symbol="AAPL",
        scenario_id="S6",
        entry_date=date(2024, 1, 2),
        entry_price=entry_price,
        quantity=10,
        holding_days=holding_days,
    )


def _make_row(**kwargs: Any) -> dict[str, Any]:
    """Build a row dict for get_exit_signal."""
    defaults: dict[str, Any] = {
        "close": 102.0,
        "rsi_2": 30.0,
    }
    defaults.update(kwargs)
    return defaults


# ── Entry signal tests ────────────────────────────────────────────────────────


def test_all_conditions_met_produces_buy(scenario: S6Reversion):
    """Full entry signal fires on the last row when all S6 conditions are met."""
    df = _make_df()
    result = scenario.generate_signals(df)
    last_action = result["action"][-1]
    assert last_action == "BUY", f"Expected BUY but got '{last_action}'"


def test_no_signal_when_ret5d_not_below_threshold(scenario: S6Reversion):
    """Condition 1 failure: ret_5d > return_threshold (-0.10)."""
    df = _make_df()
    ret = df["ret_5d"].to_list()
    ret[-1] = -0.05  # above -0.10
    df = df.with_columns(pl.Series("ret_5d", ret))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_no_signal_when_close_below_ma200(scenario: S6Reversion):
    """Condition 2 failure: close <= ma_200 (not in uptrend)."""
    df = _make_df()
    close = df["close"].to_list()
    close[-1] = 99.0  # below ma_200=100
    df = df.with_columns(pl.Series("close", close))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_no_signal_when_volume_too_low(scenario: S6Reversion):
    """Condition 3 failure: vol_ratio_20 < volume_multiplier (2.0)."""
    df = _make_df()
    vol = df["vol_ratio_20"].to_list()
    vol[-1] = 1.5
    df = df.with_columns(pl.Series("vol_ratio_20", vol))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_no_signal_when_rsi2_not_oversold(scenario: S6Reversion):
    """Condition 4 failure: rsi_2 >= rsi_oversold (10)."""
    df = _make_df()
    rsi = df["rsi_2"].to_list()
    rsi[-1] = 15.0  # >= 10
    df = df.with_columns(pl.Series("rsi_2", rsi))
    result = scenario.generate_signals(df)
    assert result["action"][-1] == ""


def test_missing_indicator_columns_returns_empty(scenario: S6Reversion):
    """When required columns are missing, all actions must be empty strings."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 10,
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(10)],
            "close": [100.0] * 10,
            # ret_5d, ma_200, vol_ratio_20, rsi_2 are missing
        }
    )
    result = scenario.generate_signals(df)
    assert all(a == "" for a in result["action"].to_list())


# ── Exit signal tests ─────────────────────────────────────────────────────────


def test_exit_take_profit_price(scenario: S6Reversion):
    """TAKE_PROFIT triggers when close >= entry_price * (1 + take_profit_pct)."""
    pos = _make_position(entry_price=100.0, holding_days=3)
    # take_profit_pct=0.05 → threshold=105
    row = _make_row(close=106.0, rsi_2=40.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_take_profit_rsi(scenario: S6Reversion):
    """TAKE_PROFIT triggers when rsi_2 >= rsi_take_profit (70) even without price target."""
    pos = _make_position(entry_price=100.0, holding_days=3)
    # close=102 is below price TP threshold (105), but rsi_2=75 >= 70
    row = _make_row(close=102.0, rsi_2=75.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_stop_loss(scenario: S6Reversion):
    """STOP_LOSS triggers when close <= entry_price * (1 + stop_loss_pct)."""
    pos = _make_position(entry_price=100.0, holding_days=2)
    # stop_loss_pct=-0.05 → threshold=95
    row = _make_row(close=94.0, rsi_2=5.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


def test_exit_time_exit(scenario: S6Reversion):
    """TIME_EXIT triggers when holding_days >= time_exit_days (10)."""
    pos = _make_position(entry_price=100.0, holding_days=10)
    # close=102 — no price or RSI take-profit, no stop-loss
    row = _make_row(close=102.0, rsi_2=40.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TIME_EXIT


def test_no_exit_within_range(scenario: S6Reversion):
    """No exit when all conditions are neutral."""
    pos = _make_position(entry_price=100.0, holding_days=3)
    row = _make_row(close=102.0, rsi_2=40.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.NO_EXIT


def test_take_profit_priority_over_stop_loss(scenario: S6Reversion):
    """TAKE_PROFIT (RSI) has higher priority than STOP_LOSS."""
    # This is an edge case: in practice both won't trigger simultaneously, but
    # the priority order must be respected if they somehow do.
    pos = _make_position(entry_price=100.0, holding_days=2)
    # RSI take-profit fires; close is also below stop (shouldn't happen in real life)
    row = _make_row(close=94.0, rsi_2=80.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


# ── Schema ────────────────────────────────────────────────────────────────────


def test_generate_signals_schema(scenario: S6Reversion):
    """Output DataFrame must have the required signal columns and correct height."""
    df = _make_df()
    result = scenario.generate_signals(df)
    for col in ("symbol", "date", "action", "scenario_id"):
        assert col in result.columns
    assert result.height == df.height
    assert all(sid == "S6" for sid in result["scenario_id"].to_list())


def test_params_loaded(scenario: S6Reversion):
    """Parameters are correctly parsed from YAML."""
    p = scenario.params
    assert isinstance(p, S6Params)
    assert p.return_threshold == pytest.approx(-0.10)
    assert p.take_profit_pct == pytest.approx(0.05)
    assert p.stop_loss_pct == pytest.approx(-0.05)
    assert p.time_exit_days == 10
    assert p.rsi_oversold == pytest.approx(10.0)
    assert p.rsi_take_profit == pytest.approx(70.0)
