"""Weekly and monthly performance report generator."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from src.backtest.metrics import compute_metrics
from src.utils.logger import get_logger

logger = get_logger()

_RESULTS_DIR = Path(__file__).parent.parent.parent / "results"


def _load_trades(start: date, end: date) -> pl.DataFrame:
    """Load completed trades from DuckDB for the given date range."""
    from src.data.repository import Repository

    try:
        with Repository() as repo:
            rows = repo._conn.execute(
                "SELECT * FROM trades WHERE exit_date >= ? AND exit_date <= ? ORDER BY exit_date",
                [start.isoformat(), end.isoformat()],
            ).fetchall()
            if not rows:
                return pl.DataFrame()
            cols = [d[0] for d in repo._conn.description or []]
            return pl.DataFrame([dict(zip(cols, r, strict=False)) for r in rows])
    except Exception as exc:
        logger.warning(f"report: failed to load trades: {exc}")
        return pl.DataFrame()


def _metrics_dict(trades: pl.DataFrame) -> dict[str, Any]:
    """Compute metrics and return as a serializable dict."""
    dummy_eq = pl.DataFrame(
        {
            "date": [date.today()],
            "portfolio_value": [1.0],
            "cash": [1.0],
        }
    )
    m = compute_metrics(trades, dummy_eq, bootstrap_samples=0)
    return {
        "trade_count": m.trade_count,
        "win_rate": round(m.win_rate, 4),
        "payoff_ratio": round(m.payoff_ratio, 4) if m.payoff_ratio != float("inf") else None,
        "profit_factor": round(m.profit_factor, 4) if m.profit_factor != float("inf") else None,
        "avg_holding_days": round(m.avg_holding_days, 1),
        "is_reliable": m.is_reliable,
        "total_pnl": round(float(trades["pnl"].sum()), 2) if not trades.is_empty() else 0.0,
    }


def generate_weekly_report(
    report_date: date | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """Generate a weekly performance report (Mon–Sun of the previous week)."""
    today = report_date or date.today()
    # Previous Monday
    week_end = today - timedelta(days=today.weekday() + 1)  # last Sunday
    week_start = week_end - timedelta(days=6)  # last Monday

    trades = _load_trades(week_start, week_end)
    by_scenario: dict[str, Any] = {}
    if not trades.is_empty() and "scenario_id" in trades.columns:
        for scen in trades["scenario_id"].unique().to_list():
            sub = trades.filter(pl.col("scenario_id") == scen)
            by_scenario[scen] = _metrics_dict(sub)

    report = {
        "type": "weekly",
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "generated_at": today.isoformat(),
        "overall": _metrics_dict(trades),
        "by_scenario": by_scenario,
    }

    if save:
        _RESULTS_DIR.mkdir(exist_ok=True)
        path = _RESULTS_DIR / f"report_weekly_{week_start}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        logger.info(f"report: weekly saved → {path}")

    return report


def generate_monthly_report(
    report_date: date | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """Generate a monthly performance report for the previous calendar month."""
    today = report_date or date.today()
    month_end = today.replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)

    trades = _load_trades(month_start, month_end)
    by_scenario: dict[str, Any] = {}
    if not trades.is_empty() and "scenario_id" in trades.columns:
        for scen in trades["scenario_id"].unique().to_list():
            sub = trades.filter(pl.col("scenario_id") == scen)
            by_scenario[scen] = _metrics_dict(sub)

    report = {
        "type": "monthly",
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "generated_at": today.isoformat(),
        "overall": _metrics_dict(trades),
        "by_scenario": by_scenario,
    }

    if save:
        _RESULTS_DIR.mkdir(exist_ok=True)
        path = _RESULTS_DIR / f"report_monthly_{month_start.strftime('%Y%m')}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        logger.info(f"report: monthly saved → {path}")

    return report
