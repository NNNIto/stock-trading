"""Tests for BacktestEngine."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from src.backtest.engine import BacktestEngine, MacroFilter
from src.backtest.execution import ExecutionConfig
from src.portfolio.sizer import FixedFractionSizer
from src.scenarios.base import ExitReason, Position, ScenarioBase, ScenarioParams

# ── Minimal stub scenario ─────────────────────────────────────────────────────


class _StubParams(ScenarioParams):
    stop_loss_pct: float = -0.10
    time_exit_days: int = 10


class _StubScenario(ScenarioBase):
    """Signal BUY on a specific set of (symbol, date) pairs; exit on time_exit_days."""

    scenario_id = "S2"  # use real scenario_id for priority logic
    params: _StubParams  # narrow base-class type for mypy

    def __init__(self, buy_signals: set[tuple[str, date]], stop_loss: float = -0.10) -> None:
        self._buy_signals = buy_signals
        self._stop_loss = stop_loss
        self.params = _StubParams(scenario_id="S2", name="stub", stop_loss_pct=stop_loss)

    def _parse_params(self, raw: dict[str, Any]) -> _StubParams:
        return _StubParams(scenario_id="S2", name="stub")

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        actions = []
        for row in data.iter_rows(named=True):
            key = (row["symbol"], row["date"])
            actions.append("BUY" if key in self._buy_signals else "")
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
        if close <= position.entry_price * (1 + self._stop_loss):
            return ExitReason.STOP_LOSS
        if position.holding_days >= self.params.time_exit_days:
            return ExitReason.TIME_EXIT
        return ExitReason.NO_EXIT


# ── Synthetic data builder ────────────────────────────────────────────────────


def _make_data(
    symbols: list[str],
    start: date,
    n_days: int,
    price: float = 100.0,
    market: str = "JP",
) -> pl.DataFrame:
    rows = []
    for sym in symbols:
        for i in range(n_days):
            d = start + timedelta(days=i)
            rows.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "adj_close": price,
                    "volume": 1_000_000,
                    "market": market,
                    # minimal indicator columns needed by _StubScenario
                    "ma_200": price,
                    "ma_200_slope": 0.5,
                    "vol_ratio_20": 2.0,
                    "high_252d": price * 1.05,
                }
            )
    return pl.DataFrame(rows)


def _make_engine(
    scenario: ScenarioBase | None = None,
    capital: float = 1_000_000,
    macro_filter: MacroFilter | None = None,
) -> BacktestEngine:
    s = scenario or _StubScenario(buy_signals=set())
    return BacktestEngine(
        scenarios=[s],
        sizer=FixedFractionSizer(fraction=0.15),
        exec_config=ExecutionConfig(slippage_pct=0.001, commission_pct=0.001, fx_cost_pct=0.005),
        initial_capital=capital,
        max_positions=7,
        random_seed=42,
        macro_filter=macro_filter,
    )


# ── Basic round-trip ──────────────────────────────────────────────────────────


def test_no_signals_equity_flat():
    data = _make_data(["AAPL"], date(2024, 1, 2), n_days=10)
    engine = _make_engine()
    result = engine.run(data, start_date=date(2024, 1, 2), end_date=date(2024, 1, 11))
    assert result.trades.is_empty()
    # equity stays at initial capital
    assert result.equity_curve["portfolio_value"][-1] == pytest.approx(1_000_000)


def _make_stub(
    buy_signals: set[tuple[str, date]], time_exit_days: int = 10, stop_loss: float = -0.10
) -> _StubScenario:
    """Construct a _StubScenario with the given time_exit_days."""
    s = _StubScenario(buy_signals=buy_signals, stop_loss=stop_loss)
    stub_params = _StubParams(
        scenario_id="S2", name="stub", stop_loss_pct=stop_loss, time_exit_days=time_exit_days
    )
    s.params = stub_params
    return s


def test_buy_signal_creates_trade():
    start = date(2024, 1, 2)
    # Signal on day 0, executed at day 1 open, time_exit at 5 holding days
    scenario = _make_stub(buy_signals={("AAPL", start)}, time_exit_days=5)
    data = _make_data(["AAPL"], start, n_days=20)
    engine = _make_engine(scenario=scenario)
    result = engine.run(data, start_date=start, end_date=start + timedelta(days=19))

    assert not result.trades.is_empty()
    assert result.trades["symbol"][0] == "AAPL"


def test_trade_pnl_zero_at_flat_price():
    """Entry and exit at same price → PnL ≈ -(fees)."""
    start = date(2024, 1, 2)
    scenario = _make_stub(buy_signals={("AAPL", start)}, time_exit_days=5)
    data = _make_data(["AAPL"], start, n_days=20, price=100.0)
    engine = _make_engine(scenario=scenario)
    result = engine.run(data, start_date=start, end_date=start + timedelta(days=19))

    assert not result.trades.is_empty()
    pnl = result.trades["pnl"][0]
    # Flat market: PnL is negative (fees + slippage)
    assert pnl < 0


def test_stop_loss_triggers():
    start = date(2024, 1, 2)
    scenario = _StubScenario(buy_signals={("AAPL", start)}, stop_loss=-0.05)

    # Price drops 10% on day 3
    rows = []
    for i in range(20):
        d = start + timedelta(days=i)
        price = 100.0 if i < 3 else 90.0  # -10% drop
        rows.append(
            {
                "symbol": "AAPL",
                "date": d,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "adj_close": price,
                "volume": 1_000_000,
                "market": "JP",
                "ma_200": 100.0,
                "ma_200_slope": 0.5,
                "vol_ratio_20": 2.0,
                "high_252d": 105.0,
            }
        )
    data = pl.DataFrame(rows)
    engine = _make_engine(scenario=scenario)
    result = engine.run(data, start_date=start, end_date=start + timedelta(days=19))

    assert not result.trades.is_empty()
    assert result.trades["exit_reason"][0] == ExitReason.STOP_LOSS


def test_max_positions_respected():
    start = date(2024, 1, 2)
    symbols = [f"SYM{i}" for i in range(10)]
    # All symbols fire BUY on day 0
    buy_signals = {(sym, start) for sym in symbols}
    scenario = _StubScenario(buy_signals=buy_signals)
    data = _make_data(symbols, start, n_days=5)
    engine = _make_engine(scenario=scenario, capital=10_000_000)
    result = engine.run(data, start_date=start, end_date=start + timedelta(days=4))

    # Max 7 positions; open_positions at any time ≤ 7
    max_open = result.equity_curve.height  # just check we ran without error
    assert max_open > 0
    # Check that no more than 7 trades were opened (10 signals but cap at 7)
    assert result.trades.height + len(result.open_positions) <= 7


def test_reproducibility_with_seed():
    start = date(2024, 1, 2)
    buy_signals = {(f"SYM{i}", start) for i in range(5)}
    scenario = _StubScenario(buy_signals=buy_signals)
    data = _make_data([f"SYM{i}" for i in range(5)], start, n_days=15)

    engine1 = _make_engine(scenario=scenario)
    engine2 = _make_engine(scenario=scenario)
    r1 = engine1.run(data, start_date=start, end_date=start + timedelta(days=14))
    r2 = engine2.run(data, start_date=start, end_date=start + timedelta(days=14))

    assert r1.trades.height == r2.trades.height
    assert r1.equity_curve["portfolio_value"][-1] == pytest.approx(
        r2.equity_curve["portfolio_value"][-1]
    )


def test_macro_filter_blocks_entries():
    start = date(2024, 1, 2)
    scenario = _StubScenario(buy_signals={("AAPL", start)})
    data = _make_data(["AAPL"], start, n_days=10)

    # Block the start date
    macro = MacroFilter(blackout_dates={start})
    engine = _make_engine(scenario=scenario, macro_filter=macro)
    result = engine.run(data, start_date=start, end_date=start + timedelta(days=9))

    assert result.trades.is_empty()


def test_equity_curve_has_expected_columns():
    data = _make_data(["AAPL"], date(2024, 1, 2), n_days=5)
    result = _make_engine().run(data, date(2024, 1, 2), date(2024, 1, 6))
    assert "date" in result.equity_curve.columns
    assert "portfolio_value" in result.equity_curve.columns
    assert "cash" in result.equity_curve.columns
