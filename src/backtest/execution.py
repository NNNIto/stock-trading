"""Execution model: fills orders at next-day open with slippage and fees."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_pct: float = 0.002  # per side (0.2%)
    commission_pct: float = 0.001  # round-trip total (0.1%)
    fx_cost_pct: float = 0.005  # per side, US stocks only (0.5%)


@dataclass(frozen=True)
class Fill:
    """Immutable record of a single executed order."""

    symbol: str
    trade_date: date
    direction: str  # 'BUY' | 'SELL'
    quantity: int
    fill_price: float  # open ± slippage
    gross_value: float  # fill_price × quantity
    commission: float  # one-way commission
    fx_cost: float  # one-way FX conversion cost (0 for JP stocks)
    net_value: float  # BUY: total outflow   SELL: total inflow
    market: str  # 'JP' | 'US'


def execute_buy(
    symbol: str,
    trade_date: date,
    open_price: float,
    capital_jpy: float,
    market: str,
    config: ExecutionConfig,
) -> Fill | None:
    """Fill a buy at next-day open after slippage.

    Computes share quantity from the JPY capital budget, accounting for
    per-side commission and FX cost so the total outflow stays within budget.
    Returns None when open_price is zero or quantity rounds to zero.
    """
    if open_price <= 0 or capital_jpy <= 0:
        return None

    fill_price = open_price * (1 + config.slippage_pct)

    # Effective cost per share including one-side commission and FX.
    per_side_commission = config.commission_pct / 2
    fx = config.fx_cost_pct if market == "US" else 0.0
    cost_per_share = fill_price * (1 + per_side_commission + fx)

    quantity = math.floor(capital_jpy / cost_per_share)
    if quantity <= 0:
        return None

    gross_value = fill_price * quantity
    commission = gross_value * per_side_commission
    fx_cost = gross_value * fx
    net_value = gross_value + commission + fx_cost  # total JPY outflow

    return Fill(
        symbol=symbol,
        trade_date=trade_date,
        direction="BUY",
        quantity=quantity,
        fill_price=fill_price,
        gross_value=gross_value,
        commission=commission,
        fx_cost=fx_cost,
        net_value=net_value,
        market=market,
    )


def execute_sell(
    symbol: str,
    trade_date: date,
    open_price: float,
    quantity: int,
    market: str,
    config: ExecutionConfig,
) -> Fill:
    """Fill a sell at next-day open after slippage.

    Returns the net JPY proceeds after slippage, commission, and FX cost.
    """
    fill_price = open_price * (1 - config.slippage_pct)
    gross_value = fill_price * quantity

    per_side_commission = config.commission_pct / 2
    fx = config.fx_cost_pct if market == "US" else 0.0

    commission = gross_value * per_side_commission
    fx_cost = gross_value * fx
    net_value = gross_value - commission - fx_cost  # total JPY inflow

    return Fill(
        symbol=symbol,
        trade_date=trade_date,
        direction="SELL",
        quantity=quantity,
        fill_price=fill_price,
        gross_value=gross_value,
        commission=commission,
        fx_cost=fx_cost,
        net_value=net_value,
        market=market,
    )
