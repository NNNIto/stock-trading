"""Tests for PortfolioManager."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.portfolio.manager import PortfolioManager


@pytest.fixture
def pm(tmp_path: Path) -> PortfolioManager:
    return PortfolioManager(tmp_path / "test.duckdb")


def test_open_and_get_position(pm: PortfolioManager) -> None:
    with pm:
        pm.open_position("AAPL", "S2", "US", date(2024, 1, 3), 100.1, 1488, mode="paper")
        pos = pm.get_position("AAPL")
        assert pos is not None
        assert pos["symbol"] == "AAPL"
        assert pos["quantity"] == 1488


def test_get_open_positions_empty(pm: PortfolioManager) -> None:
    with pm:
        df = pm.get_open_positions()
        assert df.is_empty()


def test_open_multiple_positions(pm: PortfolioManager) -> None:
    with pm:
        pm.open_position("AAPL", "S2", "US", date(2024, 1, 3), 100.0, 100)
        pm.open_position("MSFT", "S3", "US", date(2024, 1, 3), 200.0, 50)
        df = pm.get_open_positions()
        assert df.height == 2


def test_update_price_calculates_upnl(pm: PortfolioManager) -> None:
    with pm:
        pm.open_position("AAPL", "S2", "US", date(2024, 1, 3), 100.0, 100)
        pm.update_price("AAPL", 110.0)
        pos = pm.get_position("AAPL")
        assert pos is not None
        assert abs(pos["unrealized_pnl"] - 1000.0) < 0.01  # +10 × 100


def test_close_position_removes_row(pm: PortfolioManager) -> None:
    with pm:
        pm.open_position("AAPL", "S2", "US", date(2024, 1, 3), 100.0, 100)
        pm.close_position("AAPL")
        assert pm.get_position("AAPL") is None


def test_portfolio_summary(pm: PortfolioManager) -> None:
    with pm:
        pm.open_position("AAPL", "S2", "US", date(2024, 1, 3), 100.0, 100)
        pm.open_position("MSFT", "S3", "US", date(2024, 1, 3), 200.0, 50)
        s = pm.portfolio_summary()
        assert s["n_positions"] == 2
        assert s["total_cost"] > 0


def test_update_prices_bulk(pm: PortfolioManager) -> None:
    with pm:
        pm.open_position("AAPL", "S2", "US", date(2024, 1, 3), 100.0, 100)
        pm.update_prices_bulk({"AAPL": 115.0, "UNKNOWN": 50.0})  # UNKNOWN skipped
        pos = pm.get_position("AAPL")
        assert pos is not None
        assert abs(pos["current_price"] - 115.0) < 0.01
