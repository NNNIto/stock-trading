"""Performance metrics for backtest results.

All equity-curve metrics (CAGR, Sharpe, drawdown) use the daily portfolio
equity curve.  All trade-level metrics (win rate, PF, …) use the trades
DataFrame.  Both are outputs of BacktestEngine.run().
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PerformanceMetrics:
    """Comprehensive backtest performance summary."""

    # ── Return metrics ────────────────────────────────────────────────────────
    total_return_pct: float  # cumulative return over backtest period
    cagr: float  # compound annual growth rate

    # ── Risk-adjusted ─────────────────────────────────────────────────────────
    sharpe_ratio: float  # annualised Sharpe (√252 daily-return scaling)
    sortino_ratio: float  # annualised Sortino (downside deviation below rf)

    # ── Drawdown ──────────────────────────────────────────────────────────────
    max_drawdown: float  # most negative peak-to-trough fraction (e.g. -0.15)
    avg_drawdown: float  # mean of all negative drawdown values

    # ── Trade statistics ──────────────────────────────────────────────────────
    trade_count: int
    win_rate: float  # fraction of trades with pnl > 0
    payoff_ratio: float  # mean_win / |mean_loss|; inf when no losses
    profit_factor: float  # gross_profit / |gross_loss|; inf when no losses
    avg_holding_days: float

    # ── Statistical reliability ───────────────────────────────────────────────
    is_reliable: bool  # trade_count >= min_reliable_trades (default 30)
    sharpe_ci_low: float  # bootstrap 5th-percentile Sharpe
    sharpe_ci_high: float  # bootstrap 95th-percentile Sharpe

    # ── Benchmark ─────────────────────────────────────────────────────────────
    benchmark_annual_return: float | None = None
    excess_return: float | None = None  # cagr - benchmark_annual_return


# ── Main computation ──────────────────────────────────────────────────────────


def compute_metrics(
    trades: pl.DataFrame,
    equity_curve: pl.DataFrame,
    risk_free_rate: float = 0.0,
    bootstrap_samples: int = 1000,
    random_seed: int = 42,
    min_reliable_trades: int = 30,
    benchmark_annual_return: float | None = None,
) -> PerformanceMetrics:
    """Compute the full set of performance metrics.

    Args:
        trades:          Output of BacktestEngine.run().trades.
        equity_curve:    Output of BacktestEngine.run().equity_curve.
                         Must have columns: date, portfolio_value.
        risk_free_rate:  Annual risk-free rate (e.g. 0.001 for 0.1%).
        bootstrap_samples: Number of IID bootstrap resamples for Sharpe CI.
        random_seed:     Reproducibility seed for bootstrap.
        min_reliable_trades: Minimum trades for is_reliable flag.
        benchmark_annual_return: Pre-computed annual benchmark return for the
                         same period (e.g. from Nikkei or S&P500 data).
    """
    eq_metrics = _equity_metrics(equity_curve, risk_free_rate, bootstrap_samples, random_seed)
    tr_metrics = _trade_metrics(trades, min_reliable_trades)

    excess = (
        eq_metrics["cagr"] - benchmark_annual_return
        if benchmark_annual_return is not None
        else None
    )

    return PerformanceMetrics(
        total_return_pct=eq_metrics["total_return_pct"],
        cagr=eq_metrics["cagr"],
        sharpe_ratio=eq_metrics["sharpe_ratio"],
        sortino_ratio=eq_metrics["sortino_ratio"],
        max_drawdown=eq_metrics["max_drawdown"],
        avg_drawdown=eq_metrics["avg_drawdown"],
        sharpe_ci_low=eq_metrics["sharpe_ci_low"],
        sharpe_ci_high=eq_metrics["sharpe_ci_high"],
        trade_count=tr_metrics["trade_count"],
        win_rate=tr_metrics["win_rate"],
        payoff_ratio=tr_metrics["payoff_ratio"],
        profit_factor=tr_metrics["profit_factor"],
        avg_holding_days=tr_metrics["avg_holding_days"],
        is_reliable=tr_metrics["is_reliable"],
        benchmark_annual_return=benchmark_annual_return,
        excess_return=excess,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _equity_metrics(
    equity_curve: pl.DataFrame,
    risk_free_rate: float,
    bootstrap_samples: int,
    random_seed: int,
) -> dict:
    """Equity-curve based metrics."""
    if equity_curve.is_empty() or equity_curve.height < 2:
        return _zero_equity_metrics()

    eq = equity_curve.sort("date")
    prices = eq["portfolio_value"].to_numpy()

    initial, final = float(prices[0]), float(prices[-1])
    total_return = (final / initial) - 1 if initial != 0 else 0.0

    # CAGR
    first_date: date = eq["date"][0]
    last_date: date = eq["date"][-1]
    n_calendar_days = (last_date - first_date).days
    if n_calendar_days > 0 and initial != 0 and final > 0:
        cagr = (final / initial) ** (365.25 / n_calendar_days) - 1
    else:
        cagr = 0.0

    # Daily returns
    daily_ret = np.diff(prices) / prices[:-1]  # length = N-1

    rf_daily = risk_free_rate / 252
    excess_ret = daily_ret - rf_daily

    mean_excess = float(np.mean(excess_ret))
    std_ret = float(np.std(daily_ret, ddof=1)) if len(daily_ret) > 1 else 0.0
    sharpe = (mean_excess / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0

    # Sortino: downside deviation = sqrt(mean(min(excess_r, 0)^2)) * sqrt(252).
    # The mean is taken over ALL periods (not only negative ones) so the
    # denominator is consistent with the Sharpe denominator and the ratio is
    # comparable across portfolios with different win-rate characteristics.
    clipped = np.minimum(excess_ret, 0.0)
    downside_var = float(np.mean(clipped**2))
    if downside_var > 0:
        downside_std = math.sqrt(downside_var) * math.sqrt(252)
        sortino = mean_excess * 252 / downside_std
    elif mean_excess > 0:
        sortino = float("inf")  # positive return with zero downside risk
    else:
        sortino = 0.0

    # Drawdown
    running_max = np.maximum.accumulate(prices)
    drawdown = np.where(running_max > 0, (prices - running_max) / running_max, 0.0)
    max_dd = float(np.min(drawdown))
    neg_dd = drawdown[drawdown < 0]
    avg_dd = float(np.mean(neg_dd)) if len(neg_dd) > 0 else 0.0

    # Bootstrap Sharpe CI
    ci_low, ci_high = _bootstrap_sharpe_ci(daily_ret, rf_daily, bootstrap_samples, random_seed)

    return {
        "total_return_pct": total_return,
        "cagr": cagr,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_dd,
        "avg_drawdown": avg_dd,
        "sharpe_ci_low": ci_low,
        "sharpe_ci_high": ci_high,
    }


def _trade_metrics(trades: pl.DataFrame, min_reliable_trades: int) -> dict:
    """Trade-level metrics."""
    n = trades.height
    if n == 0:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,
            "avg_holding_days": 0.0,
            "is_reliable": False,
        }

    pnl_arr = trades["pnl"].to_numpy()
    wins_arr = pnl_arr[pnl_arr > 0]
    losses_arr = pnl_arr[pnl_arr < 0]

    win_rate = len(wins_arr) / n

    avg_win = float(wins_arr.mean()) if len(wins_arr) > 0 else 0.0
    avg_loss = float(losses_arr.mean()) if len(losses_arr) > 0 else 0.0
    payoff = avg_win / abs(avg_loss) if avg_loss != 0 else float("inf")

    gross_profit = float(wins_arr.sum()) if len(wins_arr) > 0 else 0.0
    gross_loss = float(losses_arr.sum()) if len(losses_arr) > 0 else 0.0
    pf = gross_profit / abs(gross_loss) if gross_loss != 0 else float("inf")

    avg_hold = float(trades["holding_days"].to_numpy().mean()) if n > 0 else 0.0

    return {
        "trade_count": n,
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "profit_factor": pf,
        "avg_holding_days": avg_hold,
        "is_reliable": n >= min_reliable_trades,
    }


def _bootstrap_sharpe_ci(
    daily_returns: np.ndarray,
    rf_daily: float,
    n_samples: int,
    seed: int,
) -> tuple[float, float]:
    """IID bootstrap confidence interval for annualised Sharpe ratio."""
    n = len(daily_returns)
    if n < 2 or n_samples < 1:
        return (0.0, 0.0)

    rng = np.random.default_rng(seed)
    sharpes: list[float] = []

    for _ in range(n_samples):
        sample = rng.choice(daily_returns, size=n, replace=True)
        excess = sample - rf_daily
        mean_e = float(np.mean(excess))
        std_s = float(np.std(sample, ddof=1))
        if std_s > 0:
            sharpes.append(mean_e / std_s * math.sqrt(252))

    if not sharpes:
        return (0.0, 0.0)

    sharpes_arr = np.array(sharpes)
    return (float(np.percentile(sharpes_arr, 5)), float(np.percentile(sharpes_arr, 95)))


def _zero_equity_metrics() -> dict:
    return {
        "total_return_pct": 0.0,
        "cagr": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "max_drawdown": 0.0,
        "avg_drawdown": 0.0,
        "sharpe_ci_low": 0.0,
        "sharpe_ci_high": 0.0,
    }


# ── Formatting helper ─────────────────────────────────────────────────────────


def format_metrics(m: PerformanceMetrics) -> str:
    """Return a human-readable metrics summary."""
    reliable = "✓" if m.is_reliable else "✗ (< 30 trades)"
    pf_str = f"{m.profit_factor:.2f}" if math.isfinite(m.profit_factor) else "∞"
    pr_str = f"{m.payoff_ratio:.2f}" if math.isfinite(m.payoff_ratio) else "∞"
    so_str = f"{m.sortino_ratio:.2f}" if math.isfinite(m.sortino_ratio) else "∞"
    bm_str = (
        f"  Benchmark CAGR:   {m.benchmark_annual_return:+.2%}\n"
        f"  Excess return:    {m.excess_return:+.2%}\n"
        if m.benchmark_annual_return is not None and m.excess_return is not None
        else ""
    )
    return (
        f"── Performance Metrics ──────────────────\n"
        f"  Total return:     {m.total_return_pct:+.2%}\n"
        f"  CAGR:             {m.cagr:+.2%}\n"
        f"{bm_str}"
        f"  Sharpe:           {m.sharpe_ratio:.2f}  [{m.sharpe_ci_low:.2f}, {m.sharpe_ci_high:.2f}] 90% CI\n"
        f"  Sortino:          {so_str}\n"
        f"  Max drawdown:     {m.max_drawdown:.2%}\n"
        f"  Avg drawdown:     {m.avg_drawdown:.2%}\n"
        f"── Trades ({m.trade_count}, reliable: {reliable}) ────\n"
        f"  Win rate:         {m.win_rate:.1%}\n"
        f"  Payoff ratio:     {pr_str}\n"
        f"  Profit factor:    {pf_str}\n"
        f"  Avg holding:      {m.avg_holding_days:.1f} days\n"
        f"─────────────────────────────────────────"
    )
