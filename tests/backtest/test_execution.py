"""Tests for execution model."""

from __future__ import annotations

from datetime import date

import pytest

from src.backtest.execution import ExecutionConfig, execute_buy, execute_sell

_DATE = date(2024, 1, 10)
_CFG = ExecutionConfig(slippage_pct=0.002, commission_pct=0.001, fx_cost_pct=0.005)


# ── execute_buy ───────────────────────────────────────────────────────────────


def test_buy_fill_price_includes_slippage():
    fill = execute_buy(
        "AAPL", _DATE, open_price=100.0, capital_jpy=500_000, market="US", config=_CFG
    )
    assert fill is not None
    assert fill.fill_price == pytest.approx(100.0 * 1.002)


def test_buy_quantity_fits_within_capital():
    fill = execute_buy(
        "AAPL", _DATE, open_price=100.0, capital_jpy=500_000, market="US", config=_CFG
    )
    assert fill is not None
    assert fill.net_value <= 500_000


def test_buy_net_value_includes_commission_and_fx():
    fill = execute_buy(
        "AAPL", _DATE, open_price=100.0, capital_jpy=500_000, market="US", config=_CFG
    )
    assert fill is not None
    expected = fill.gross_value + fill.commission + fill.fx_cost
    assert fill.net_value == pytest.approx(expected)


def test_buy_jp_stock_no_fx_cost():
    fill = execute_buy(
        "7203.T", _DATE, open_price=2_000.0, capital_jpy=300_000, market="JP", config=_CFG
    )
    assert fill is not None
    assert fill.fx_cost == 0.0


def test_buy_us_stock_has_fx_cost():
    fill = execute_buy(
        "AAPL", _DATE, open_price=100.0, capital_jpy=500_000, market="US", config=_CFG
    )
    assert fill is not None
    assert fill.fx_cost > 0


def test_buy_returns_none_on_zero_price():
    assert (
        execute_buy("AAPL", _DATE, open_price=0.0, capital_jpy=500_000, market="US", config=_CFG)
        is None
    )


def test_buy_returns_none_on_zero_capital():
    assert (
        execute_buy("AAPL", _DATE, open_price=100.0, capital_jpy=0.0, market="US", config=_CFG)
        is None
    )


def test_buy_returns_none_when_too_expensive():
    # 1 share costs ~100 * 1.002 * 1.0055 ≈ 100.75 JPY, budget only 10 JPY
    assert (
        execute_buy("AAPL", _DATE, open_price=100.0, capital_jpy=10.0, market="US", config=_CFG)
        is None
    )


def test_buy_direction():
    fill = execute_buy(
        "AAPL", _DATE, open_price=100.0, capital_jpy=500_000, market="US", config=_CFG
    )
    assert fill is not None
    assert fill.direction == "BUY"


def test_buy_commission_is_half_round_trip():
    fill = execute_buy(
        "AAPL", _DATE, open_price=100.0, capital_jpy=500_000, market="US", config=_CFG
    )
    assert fill is not None
    expected_commission = fill.gross_value * (_CFG.commission_pct / 2)
    assert fill.commission == pytest.approx(expected_commission)


# ── execute_sell ──────────────────────────────────────────────────────────────


def test_sell_fill_price_includes_slippage():
    fill = execute_sell("AAPL", _DATE, open_price=110.0, quantity=100, market="US", config=_CFG)
    assert fill.fill_price == pytest.approx(110.0 * (1 - 0.002))


def test_sell_net_value_is_proceeds_minus_costs():
    fill = execute_sell("AAPL", _DATE, open_price=110.0, quantity=100, market="US", config=_CFG)
    expected = fill.gross_value - fill.commission - fill.fx_cost
    assert fill.net_value == pytest.approx(expected)


def test_sell_jp_no_fx():
    fill = execute_sell("7203.T", _DATE, open_price=2_500.0, quantity=100, market="JP", config=_CFG)
    assert fill.fx_cost == 0.0


def test_sell_direction():
    fill = execute_sell("AAPL", _DATE, open_price=110.0, quantity=100, market="US", config=_CFG)
    assert fill.direction == "SELL"


def test_round_trip_cost_is_within_spec():
    """Round-trip total cost should be ≈ 2*slippage + commission + 2*fx (US)."""
    capital = 1_000_000.0
    open_buy = 100.0

    buy = execute_buy(
        "AAPL", _DATE, open_price=open_buy, capital_jpy=capital, market="US", config=_CFG
    )
    assert buy is not None

    # Simulate flat exit: same price as entry
    sell = execute_sell(
        "AAPL", _DATE, open_price=open_buy, quantity=buy.quantity, market="US", config=_CFG
    )

    total_fees = buy.commission + buy.fx_cost + sell.commission + sell.fx_cost
    slippage_cost = (buy.fill_price - open_buy + open_buy - sell.fill_price) * buy.quantity
    total_cost = total_fees + slippage_cost
    position_value = open_buy * buy.quantity

    # Round-trip cost ≈ 0.2%*2 slippage + 0.1% commission + 0.5%*2 FX = 1.5%
    cost_pct = total_cost / position_value
    assert 0.01 < cost_pct < 0.02  # between 1% and 2%
