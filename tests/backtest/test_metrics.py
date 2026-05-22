"""Tests for performance metrics."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from src.backtest.metrics import compute_metrics, format_metrics

# ── Synthetic data builders ───────────────────────────────────────────────────


def _equity(values: list[float], start: date = date(2020, 1, 2)) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(values))]
    return pl.DataFrame({"date": dates, "portfolio_value": values, "cash": values})


def _trades(pnl_list: list[float], holding: int = 10) -> pl.DataFrame:
    n = len(pnl_list)
    start = date(2020, 1, 2)
    return pl.DataFrame(
        {
            "trade_id": [f"t{i}" for i in range(n)],
            "mode": ["backtest"] * n,
            "scenario_id": ["S2"] * n,
            "scenario_version": [""] * n,
            "symbol": ["AAPL"] * n,
            "market": ["US"] * n,
            "entry_date": [start] * n,
            "entry_price": [100.0] * n,
            "exit_date": [start + timedelta(days=holding)] * n,
            "exit_price": [100.0 + p / 100 for p in pnl_list],
            "quantity": [100] * n,
            "fees": [10.0] * n,
            "pnl": pnl_list,
            "pnl_pct": [p / 10_000 for p in pnl_list],
            "holding_days": [holding] * n,
            "exit_reason": ["time_exit"] * n,
        }
    )


# ── Total return & CAGR ───────────────────────────────────────────────────────


def test_total_return_flat():
    eq = _equity([1_000_000.0] * 365)
    m = compute_metrics(_trades([]), eq)
    assert m.total_return_pct == pytest.approx(0.0, abs=1e-9)


def test_total_return_positive():
    # 10% gain over ~252 days
    eq = _equity([1_000_000.0 + i * 397.0 for i in range(253)])
    m = compute_metrics(_trades([]), eq)
    assert m.total_return_pct == pytest.approx(0.10, rel=0.01)


def test_cagr_one_year_10pct():
    # Flat → 10% over exactly 252 trading days + calendar
    n = 366  # ~1 year of calendar days
    initial = 1_000_000.0
    final = initial * 1.10
    values = [initial + (final - initial) * i / (n - 1) for i in range(n)]
    eq = _equity(values)
    m = compute_metrics(_trades([]), eq)
    assert m.cagr == pytest.approx(0.10, rel=0.05)  # within 5%


# ── Sharpe & Sortino ──────────────────────────────────────────────────────────


def test_sharpe_zero_variance_equity():
    eq = _equity([1_000_000.0] * 50)
    m = compute_metrics(_trades([]), eq)
    assert m.sharpe_ratio == 0.0


def test_sharpe_positive_for_uptrend():
    # Steady uptrend → positive Sharpe
    eq = _equity([1_000_000.0 + i * 500 for i in range(252)])
    m = compute_metrics(_trades([]), eq)
    assert m.sharpe_ratio > 0


def test_sortino_geq_sharpe_for_uptrend():
    # Sortino ≥ Sharpe when downside vol ≤ total vol.
    # Use a noisy uptrend so negative days exist (denominator > 0).
    import numpy as np

    rng = np.random.default_rng(0)
    prices = [1_000_000.0]
    for _ in range(251):
        prices.append(prices[-1] * (1 + 0.001 + rng.normal(0, 0.005)))
    eq = _equity(prices)
    m = compute_metrics(_trades([]), eq)
    assert m.sortino_ratio >= m.sharpe_ratio - 0.01


def test_sortino_inf_for_zero_downside():
    # Perfectly monotone uptrend → no negative returns → Sortino = inf
    eq = _equity([1_000_000.0 + i * 1000 for i in range(252)])
    m = compute_metrics(_trades([]), eq)
    assert math.isinf(m.sortino_ratio)


# ── Drawdown ──────────────────────────────────────────────────────────────────


def test_max_drawdown_flat():
    eq = _equity([1_000_000.0] * 100)
    m = compute_metrics(_trades([]), eq)
    assert m.max_drawdown == pytest.approx(0.0, abs=1e-9)


def test_max_drawdown_known_drop():
    # Rise to 1.2M then fall to 0.9M → DD = (0.9 - 1.2) / 1.2 = -25%
    values = [1_000_000.0] * 50 + [1_200_000.0] * 50 + [900_000.0] * 50
    eq = _equity(values)
    m = compute_metrics(_trades([]), eq)
    assert m.max_drawdown == pytest.approx(-0.25, rel=0.01)


def test_avg_drawdown_negative():
    values = [1_000_000.0] * 10 + [900_000.0] * 10 + [1_100_000.0] * 10
    eq = _equity(values)
    m = compute_metrics(_trades([]), eq)
    assert m.avg_drawdown < 0


# ── Trade statistics ──────────────────────────────────────────────────────────


def test_win_rate_all_wins():
    m = compute_metrics(_trades([100.0] * 10), _equity([1_000_000.0] * 50))
    assert m.win_rate == pytest.approx(1.0)


def test_win_rate_all_losses():
    m = compute_metrics(_trades([-100.0] * 10), _equity([1_000_000.0] * 50))
    assert m.win_rate == pytest.approx(0.0)


def test_win_rate_mixed():
    pnl = [100.0] * 3 + [-50.0] * 2  # 3 wins out of 5
    m = compute_metrics(_trades(pnl), _equity([1_000_000.0] * 50))
    assert m.win_rate == pytest.approx(0.6)


def test_payoff_ratio():
    pnl = [200.0] * 5 + [-100.0] * 5  # avg_win=200, avg_loss=100 → ratio=2.0
    m = compute_metrics(_trades(pnl), _equity([1_000_000.0] * 50))
    assert m.payoff_ratio == pytest.approx(2.0, rel=0.01)


def test_payoff_ratio_no_losses():
    m = compute_metrics(_trades([100.0] * 5), _equity([1_000_000.0] * 50))
    assert math.isinf(m.payoff_ratio)


def test_profit_factor():
    pnl = [200.0] * 5 + [-100.0] * 5  # gross_profit=1000, gross_loss=500 → PF=2.0
    m = compute_metrics(_trades(pnl), _equity([1_000_000.0] * 50))
    assert m.profit_factor == pytest.approx(2.0, rel=0.01)


def test_avg_holding_days():
    trades = _trades([100.0] * 5, holding=15)
    m = compute_metrics(trades, _equity([1_000_000.0] * 50))
    assert m.avg_holding_days == pytest.approx(15.0)


# ── Reliability ───────────────────────────────────────────────────────────────


def test_is_reliable_below_threshold():
    m = compute_metrics(_trades([1.0] * 29), _equity([1_000_000.0] * 50))
    assert not m.is_reliable


def test_is_reliable_at_threshold():
    m = compute_metrics(_trades([1.0] * 30), _equity([1_000_000.0] * 50))
    assert m.is_reliable


# ── Bootstrap CI ─────────────────────────────────────────────────────────────


def test_bootstrap_ci_ordered():
    eq = _equity([1_000_000.0 + i * 200 for i in range(252)])
    m = compute_metrics(_trades([]), eq, bootstrap_samples=200, random_seed=42)
    assert m.sharpe_ci_low <= m.sharpe_ratio <= m.sharpe_ci_high


def test_bootstrap_ci_reproducible():
    eq = _equity([1_000_000.0 + i * 200 for i in range(252)])
    m1 = compute_metrics(_trades([]), eq, bootstrap_samples=100, random_seed=7)
    m2 = compute_metrics(_trades([]), eq, bootstrap_samples=100, random_seed=7)
    assert m1.sharpe_ci_low == m2.sharpe_ci_low
    assert m1.sharpe_ci_high == m2.sharpe_ci_high


# ── Benchmark ─────────────────────────────────────────────────────────────────


def test_excess_return_computed():
    eq = _equity([1_000_000.0 + i * 1000 for i in range(366)])
    m = compute_metrics(_trades([]), eq, benchmark_annual_return=0.05)
    assert m.benchmark_annual_return == pytest.approx(0.05)
    assert m.excess_return is not None
    assert m.excess_return == pytest.approx(m.cagr - 0.05, rel=0.01)


def test_excess_return_none_without_benchmark():
    eq = _equity([1_000_000.0] * 50)
    m = compute_metrics(_trades([]), eq)
    assert m.excess_return is None


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_empty_equity_curve():
    empty_eq = pl.DataFrame({"date": [], "portfolio_value": [], "cash": []}).cast(
        {"portfolio_value": pl.Float64, "cash": pl.Float64, "date": pl.Date}
    )
    m = compute_metrics(_trades([]), empty_eq)
    assert m.total_return_pct == 0.0
    assert m.sharpe_ratio == 0.0


def test_empty_trades():
    eq = _equity([1_000_000.0] * 50)
    m = compute_metrics(_trades([]), eq)
    assert m.trade_count == 0
    assert m.win_rate == 0.0
    assert not m.is_reliable


# ── format_metrics ────────────────────────────────────────────────────────────


def test_format_metrics_runs():
    eq = _equity([1_000_000.0 + i * 500 for i in range(252)])
    m = compute_metrics(_trades([100.0] * 35 + [-50.0] * 5), eq)
    text = format_metrics(m)
    assert "CAGR" in text
    assert "Sharpe" in text
    assert "Win rate" in text
