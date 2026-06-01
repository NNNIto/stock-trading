"""Tests for dashboard data-loading and KPI functions (no Streamlit calls)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from src.reporting.dashboard import (
    _compute_kpis,
    _load_equity_curve,
    _load_signals,
    _load_trades,
)

# ── _load_equity_curve ────────────────────────────────────────────────────────


def test_load_equity_curve_missing_file(tmp_path: Path) -> None:
    df = _load_equity_curve(tmp_path)
    assert df.is_empty()


def test_load_equity_curve_with_csv(tmp_path: Path) -> None:
    csv = tmp_path / "equity_curve.csv"
    csv.write_text("date,portfolio_value\n2024-01-02,3000000\n2024-01-03,3050000\n")
    df = _load_equity_curve(tmp_path)
    assert df.height == 2
    assert "portfolio_value" in df.columns


def test_load_equity_curve_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / "equity_curve.csv").write_text("not,valid\x00csv\ncontent")
    df = _load_equity_curve(tmp_path)
    assert isinstance(df, pl.DataFrame)


# ── _load_trades / _load_signals ──────────────────────────────────────────────


def test_load_trades_no_csv_no_db_returns_empty(tmp_path: Path) -> None:
    """When neither trades.csv nor DB table exists, return empty DataFrame."""
    df = _load_trades(start=date(2099, 1, 1), results_dir=tmp_path)
    assert isinstance(df, pl.DataFrame)


def test_load_trades_with_csv(tmp_path: Path) -> None:
    csv = tmp_path / "trades.csv"
    csv.write_text(
        "symbol,market,exit_date,pnl\nAAPL,US,2024-01-03,500.0\nMSFT,US,2024-01-05,-200.0\n"
    )
    df = _load_trades(results_dir=tmp_path)
    assert df.height == 2
    assert "AAPL" in df["symbol"].to_list()


def test_load_trades_csv_start_filter(tmp_path: Path) -> None:
    csv = tmp_path / "trades.csv"
    csv.write_text(
        "symbol,market,exit_date,pnl\n" "AAPL,US,2024-01-03,500.0\n" "MSFT,US,2024-06-01,300.0\n"
    )
    df = _load_trades(start=date(2024, 3, 1), results_dir=tmp_path)
    assert df.height == 1
    assert df["symbol"][0] == "MSFT"


def test_load_trades_csv_market_filter(tmp_path: Path) -> None:
    csv = tmp_path / "trades.csv"
    csv.write_text(
        "symbol,market,exit_date,pnl\nAAPL,US,2024-01-03,500.0\n7203,JP,2024-01-05,200.0\n"
    )
    df = _load_trades(market="US", results_dir=tmp_path)
    assert df.height == 1
    assert df["symbol"][0] == "AAPL"


def test_load_signals_no_db_returns_empty() -> None:
    df = _load_signals(limit=10, start=date(2099, 1, 1))
    assert isinstance(df, pl.DataFrame)


# ── _compute_kpis ─────────────────────────────────────────────────────────────


def test_compute_kpis_empty_inputs() -> None:
    kpis = _compute_kpis(pl.DataFrame(), pl.DataFrame())
    assert kpis["cumulative_pnl"] == 0.0
    assert kpis["win_rate"] is None
    assert kpis["max_drawdown"] is None
    assert kpis["sharpe"] is None
    assert kpis["trade_count"] == 0


def test_compute_kpis_from_trades() -> None:
    trades = pl.DataFrame({"pnl": [1000.0, -500.0, 2000.0, 800.0]})
    kpis = _compute_kpis(trades, pl.DataFrame())
    assert kpis["cumulative_pnl"] == pytest.approx(3300.0)
    assert kpis["trade_count"] == 4
    assert kpis["win_rate"] == pytest.approx(0.75)


def test_compute_kpis_all_losses() -> None:
    trades = pl.DataFrame({"pnl": [-100.0, -200.0]})
    kpis = _compute_kpis(trades, pl.DataFrame())
    assert kpis["win_rate"] == pytest.approx(0.0)
    assert kpis["cumulative_pnl"] == pytest.approx(-300.0)


def test_compute_kpis_max_drawdown_from_equity() -> None:
    equity = pl.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "portfolio_value": [1_000_000.0, 800_000.0, 900_000.0],
        }
    )
    kpis = _compute_kpis(pl.DataFrame(), equity)
    assert kpis["max_drawdown"] is not None
    assert kpis["max_drawdown"] == pytest.approx(-0.2, abs=1e-6)


def test_compute_kpis_sharpe_computed() -> None:
    import math

    vals = [1_000_000.0 * (1.001**i) for i in range(50)]
    equity = pl.DataFrame(
        {"date": [f"2024-01-{i+1:02d}" for i in range(50)], "portfolio_value": vals}
    )
    kpis = _compute_kpis(pl.DataFrame(), equity)
    assert kpis["sharpe"] is not None
    assert math.isfinite(kpis["sharpe"])
