"""Tests for S4 – Post-Earnings Announcement Drift (PEAD) scenario."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from src.scenarios.base import ExitReason, Position
from src.scenarios.s4_pead import S4PEAD

# ── Fixtures ──────────────────────────────────────────────────────────────────

S4_CONFIG: dict[str, Any] = {
    "scenario_id": "S4",
    "name": "決算後ドリフト（PEAD）",
    "enabled": True,
    "version": "1.0.0",
    "parameters": {
        "gap_up_pct": 0.02,
        "earnings_day_return_pct": 0.05,
        "volume_multiplier": 3.0,
        "surprise_threshold_pct": 0.0,
        "trend_ma_days": 200,
        "trend_slope_window": 20,
        "entry_delay_days": 2,
        "time_exit_days": 60,
        "stop_loss_pct": -0.10,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": -0.15,
        "pre_earnings_exit_days": 5,
        "use_eps_filter": True,
    },
    "change_log": [],
}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    p = tmp_path / "s4.yaml"
    p.write_text(yaml.dump(S4_CONFIG))
    return p


@pytest.fixture
def scenario(config_path: Path) -> S4PEAD:
    return S4PEAD(config_path=config_path)


@pytest.fixture
def scenario_no_eps_filter(tmp_path: Path) -> S4PEAD:
    cfg = dict(S4_CONFIG)
    cfg["parameters"] = dict(S4_CONFIG["parameters"])
    cfg["parameters"]["use_eps_filter"] = False
    p = tmp_path / "s4_no_eps.yaml"
    p.write_text(yaml.dump(cfg))
    return S4PEAD(config_path=p)


# ── Helper: build a 300-row base DataFrame ────────────────────────────────────

_N = 300
_ENTRY_DELAY = 2  # entry_delay_days


def _make_base_df(n: int = _N) -> dict[str, list]:
    """Create n rows of default values that do NOT trigger a signal."""
    start = date(2023, 1, 3)
    dates = [start + timedelta(days=i) for i in range(n)]
    return {
        "symbol": ["AAPL"] * n,
        "date": dates,
        "close": [150.0] * n,
        "open": [149.0] * n,  # gap_up = (149-150)/150 ≈ -0.67% → below gap_up_pct
        "high": [152.0] * n,
        "low": [148.0] * n,
        "is_earnings_day": [False] * n,
        "vol_ratio_20": [1.0] * n,  # below volume_multiplier=3.0
        "eps_surprise_pct": [0.05] * n,  # positive surprise
        "ma_200": [140.0] * n,
        "ma_200_slope": [0.5] * n,
        "ma_20": [148.0] * n,
        "ma_50": [145.0] * n,
    }


def _make_signal_df(
    n: int = _N,
    target_row: int = 30,
    eps_surprise: float | None = 0.05,
) -> dict[str, list]:
    """All entry conditions met: earnings at target_row - entry_delay_days, BUY at target_row.

    entry_delay_days=2, so earnings day is at target_row - 2.
    """
    d = _make_base_df(n)
    er = target_row - _ENTRY_DELAY  # earnings row index
    tr = target_row  # today (entry row)

    # ── Earnings day (er) conditions ──────────────────────────────────────────
    d["is_earnings_day"][er] = True

    # Previous close for gap_up calc: close[er-1]
    d["close"][er - 1] = 100.0
    d["open"][er] = 103.0  # gap_up = (103-100)/100 = 0.03 >= 0.02
    d["close"][er] = 105.0  # day_return = (105-100)/100 = 0.05 >= 0.05
    d["vol_ratio_20"][er] = 4.0  # >= 3.0

    if eps_surprise is not None:
        d["eps_surprise_pct"][er] = eps_surprise
    else:
        d["eps_surprise_pct"][er] = None  # type: ignore[call-overload]

    # ── Entry day (tr) conditions ─────────────────────────────────────────────
    # Previous close for gap_up calc at tr (must not re-trigger by accident)
    d["close"][tr - 1] = 106.0
    d["open"][tr] = 107.0
    d["close"][tr] = 155.0  # > ma_200=140
    d["ma_200"][tr] = 140.0
    d["ma_200_slope"][tr] = 0.5

    # Between er and tr, set intermediate rows to something safe
    for i in range(er + 1, tr):
        d["close"][i] = 110.0 + i * 0.1

    return d


def _to_nullable_df(d: dict[str, list]) -> pl.DataFrame:
    """Build DataFrame handling None values in eps_surprise_pct."""
    return pl.DataFrame(
        d,
        schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "close": pl.Float64,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "is_earnings_day": pl.Boolean,
            "vol_ratio_20": pl.Float64,
            "eps_surprise_pct": pl.Float64,
            "ma_200": pl.Float64,
            "ma_200_slope": pl.Float64,
            "ma_20": pl.Float64,
            "ma_50": pl.Float64,
        },
    )


# ── Entry signal tests ────────────────────────────────────────────────────────


def test_buy_signal_all_conditions_with_eps(scenario: S4PEAD) -> None:
    """All conditions met with positive EPS surprise → BUY at target_row."""
    d = _make_signal_df(target_row=30, eps_surprise=0.10)
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == "BUY"


def test_buy_signal_eps_null_fallback(scenario: S4PEAD) -> None:
    """EPS data is null (not available) → fallback to gap+volume only → BUY."""
    d = _make_signal_df(target_row=30, eps_surprise=None)
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == "BUY"


def test_buy_signal_use_eps_filter_false(scenario_no_eps_filter: S4PEAD) -> None:
    """use_eps_filter=False → EPS completely ignored → BUY regardless of EPS."""
    d = _make_signal_df(target_row=30, eps_surprise=-0.10)  # negative EPS
    df = _to_nullable_df(d)
    result = scenario_no_eps_filter.generate_signals(df)
    assert result["action"][30] == "BUY"


def test_no_signal_when_not_earnings_day(scenario: S4PEAD) -> None:
    """Condition 1 violated: is_earnings_day=False at the earnings-day offset."""
    d = _make_signal_df(target_row=30)
    er = 30 - _ENTRY_DELAY
    d["is_earnings_day"][er] = False
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


def test_no_signal_when_gap_up_insufficient(scenario: S4PEAD) -> None:
    """Condition 2 violated: gap_up < gap_up_pct=0.02."""
    d = _make_signal_df(target_row=30)
    er = 30 - _ENTRY_DELAY
    # gap_up = (open - prev_close) / prev_close = (100.5 - 100) / 100 = 0.005 < 0.02
    d["close"][er - 1] = 100.0
    d["open"][er] = 100.5
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


def test_no_signal_when_day_return_insufficient(scenario: S4PEAD) -> None:
    """Condition 3 violated: day_return < earnings_day_return_pct=0.05."""
    d = _make_signal_df(target_row=30)
    er = 30 - _ENTRY_DELAY
    # day_return = (close - prev_close) / prev_close = (103 - 100)/100 = 0.03 < 0.05
    d["close"][er - 1] = 100.0
    d["open"][er] = 103.0  # gap_up OK: (103-100)/100 = 0.03 >= 0.02
    d["close"][er] = 103.0  # day_return = 0.03 < 0.05
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


def test_no_signal_when_volume_insufficient(scenario: S4PEAD) -> None:
    """Condition 4 violated: vol_ratio_20 < volume_multiplier=3.0."""
    d = _make_signal_df(target_row=30)
    er = 30 - _ENTRY_DELAY
    d["vol_ratio_20"][er] = 2.0
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


def test_no_signal_when_eps_negative_with_filter(scenario: S4PEAD) -> None:
    """Condition 5 violated: eps_surprise_pct <= surprise_threshold_pct=0.0 with use_eps_filter=True."""
    d = _make_signal_df(target_row=30, eps_surprise=-0.05)
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


def test_no_signal_when_close_below_ma200(scenario: S4PEAD) -> None:
    """Condition 6 violated: close < ma_200 on entry day."""
    d = _make_signal_df(target_row=30)
    d["close"][30] = 135.0  # below ma_200=140
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


def test_no_signal_when_ma200_slope_nonpositive(scenario: S4PEAD) -> None:
    """Condition 7 violated: ma_200_slope <= 0 on entry day."""
    d = _make_signal_df(target_row=30)
    d["ma_200_slope"][30] = -0.1
    df = _to_nullable_df(d)
    result = scenario.generate_signals(df)
    assert result["action"][30] == ""


# ── Exit signal tests ─────────────────────────────────────────────────────────


def _make_position(
    entry_price: float = 100.0,
    peak_price: float = 100.0,
    holding_days: int = 0,
    metadata: dict | None = None,
) -> Position:
    return Position(
        symbol="AAPL",
        scenario_id="S4",
        entry_date=date(2024, 1, 2),
        entry_price=entry_price,
        quantity=10,
        peak_price=peak_price,
        holding_days=holding_days,
        metadata=metadata or {},
    )


def _make_row(current_date: date = date(2024, 3, 1), **kwargs: float) -> Any:
    defaults: dict[str, Any] = {
        "close": 100.0,
        "ma_200": 90.0,
        "ma_200_slope": 0.5,
        "date": current_date,
    }
    defaults.update(kwargs)
    df = pl.DataFrame(
        {k: [v] for k, v in defaults.items()},
        schema={
            "close": pl.Float64,
            "ma_200": pl.Float64,
            "ma_200_slope": pl.Float64,
            "date": pl.Date,
        },
    )
    return df.row(0, named=True)


def test_exit_stop_loss(scenario: S4PEAD) -> None:
    """STOP_LOSS triggered by close <= entry * (1 - 0.10) = 90."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=89.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


def test_exit_take_profit(scenario: S4PEAD) -> None:
    """TAKE_PROFIT triggered by close >= entry * 1.25 = 125."""
    pos = _make_position(entry_price=100.0)
    row = _make_row(close=126.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


def test_exit_trailing_stop(scenario: S4PEAD) -> None:
    """TRAILING_STOP triggered by close <= peak_price * (1 - 0.15)."""
    # peak=120, trailing_stop at 120 * 0.85 = 102
    pos = _make_position(entry_price=100.0, peak_price=120.0)
    row = _make_row(close=101.0)  # 101 < 102 → trailing stop
    assert scenario.get_exit_signal(pos, row) == ExitReason.TRAILING_STOP


def test_exit_pre_earnings(scenario: S4PEAD) -> None:
    """PRE_EARNINGS triggered when next_report_date is within pre_earnings_exit_days=5."""
    current_date = date(2024, 3, 1)
    next_report = date(2024, 3, 4)  # 3 days away → <= 5
    pos = _make_position(
        entry_price=100.0,
        metadata={"next_report_date": next_report},
    )
    row = _make_row(current_date=current_date, close=110.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.PRE_EARNINGS


def test_no_exit_pre_earnings_when_far(scenario: S4PEAD) -> None:
    """PRE_EARNINGS NOT triggered when next_report_date is more than 5 days away."""
    current_date = date(2024, 3, 1)
    next_report = date(2024, 3, 15)  # 14 days away → > 5
    pos = _make_position(
        entry_price=100.0,
        holding_days=5,
        metadata={"next_report_date": next_report},
    )
    row = _make_row(current_date=current_date, close=110.0)
    result = scenario.get_exit_signal(pos, row)
    assert result == ExitReason.NO_EXIT


def test_exit_time_exit(scenario: S4PEAD) -> None:
    """TIME_EXIT triggered when holding_days >= time_exit_days=60."""
    pos = _make_position(entry_price=100.0, holding_days=60)
    row = _make_row(close=105.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TIME_EXIT


def test_no_exit_when_all_conditions_fine(scenario: S4PEAD) -> None:
    """NO_EXIT when no condition is triggered."""
    pos = _make_position(entry_price=100.0, peak_price=110.0, holding_days=10)
    row = _make_row(close=108.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.NO_EXIT


def test_exit_priority_stop_loss_over_trailing(scenario: S4PEAD) -> None:
    """STOP_LOSS has higher priority than TRAILING_STOP."""
    # entry=100, stop at 90; peak=200, trailing at 170
    # close=89 → triggers both stop and trailing, stop wins
    pos = _make_position(entry_price=100.0, peak_price=200.0)
    row = _make_row(close=89.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.STOP_LOSS


def test_exit_priority_take_profit_over_trailing(scenario: S4PEAD) -> None:
    """TAKE_PROFIT has higher priority than TRAILING_STOP."""
    # entry=100, take_profit at 125; peak=130, trailing at 110.5
    # close=126 → triggers both take_profit and trailing (126 > 110.5), take_profit wins
    pos = _make_position(entry_price=100.0, peak_price=130.0)
    row = _make_row(close=126.0)
    assert scenario.get_exit_signal(pos, row) == ExitReason.TAKE_PROFIT


# ── Missing is_earnings_day column → empty signals ────────────────────────────


def test_missing_is_earnings_day_returns_empty_signals(scenario: S4PEAD) -> None:
    """If is_earnings_day column is absent, generate_signals returns all ''."""
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * 5,
            "date": [date(2024, 1, i + 1) for i in range(5)],
            "close": [150.0] * 5,
            "open": [149.0] * 5,
            "ma_200": [140.0] * 5,
            "ma_200_slope": [0.5] * 5,
            "vol_ratio_20": [4.0] * 5,
            # is_earnings_day intentionally omitted
        }
    )
    result = scenario.generate_signals(df)
    assert list(result["action"]) == [""] * 5
    assert list(result["scenario_id"]) == ["S4"] * 5


# ── Schema correctness ────────────────────────────────────────────────────────


def test_generate_signals_schema(scenario: S4PEAD) -> None:
    """Output always has required columns."""
    df = _to_nullable_df(_make_base_df(10))
    result = scenario.generate_signals(df)
    for col in ("symbol", "date", "action", "scenario_id"):
        assert col in result.columns, f"Missing column: {col}"
    assert result.height == 10


def test_generate_signals_no_spurious_buys(scenario: S4PEAD) -> None:
    """Base DataFrame with no earnings days → no BUY signals."""
    df = _to_nullable_df(_make_base_df(50))
    result = scenario.generate_signals(df)
    assert all(a == "" for a in result["action"].to_list())
