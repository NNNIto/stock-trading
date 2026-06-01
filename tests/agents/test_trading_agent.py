"""Tests for TradingAgent (all run with dry_run=True)."""

from __future__ import annotations

import sys
import types
from datetime import date

import pytest

from src.agents.trading_agent import TradingAgent


@pytest.fixture()
def mock_daily_signals(monkeypatch):
    """Inject a mock daily_signals module so no real DB or network calls happen."""
    calls: list[dict] = []
    mod = types.ModuleType("daily_signals")
    mod.run = lambda signal_date, dry_run=True, **kw: calls.append(  # type: ignore[attr-defined]
        {"date": signal_date, "dry_run": dry_run}
    )
    monkeypatch.setitem(sys.modules, "daily_signals", mod)
    return calls


def test_default_dry_run() -> None:
    assert TradingAgent().dry_run is True


def test_run_daily_delegates_to_daily_signals(mock_daily_signals) -> None:
    agent = TradingAgent(dry_run=True)
    agent.run_daily(date(2024, 1, 3))
    assert mock_daily_signals == [{"date": date(2024, 1, 3), "dry_run": True}]


def test_run_daily_uses_today_when_no_date(mock_daily_signals) -> None:
    agent = TradingAgent(dry_run=True)
    agent.run_daily()
    assert len(mock_daily_signals) == 1
    assert mock_daily_signals[0]["dry_run"] is True
    assert isinstance(mock_daily_signals[0]["date"], date)


def test_run_daily_passes_dry_run_false(mock_daily_signals) -> None:
    agent = TradingAgent(dry_run=False)
    agent.run_daily(date(2024, 1, 3))
    assert mock_daily_signals[0]["dry_run"] is False


def test_execute_order_dry_run_creates_csv(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.agents.trading_agent._RESULTS_DIR", tmp_path)
    agent = TradingAgent(dry_run=True)
    signal = {"symbol": "AAPL", "scenario_id": "S2", "market": "US", "price": 178.5}
    agent.execute_order(signal, quantity=84)
    csvs = list(tmp_path.glob("signals_*.csv"))
    assert len(csvs) == 1
    content = csvs[0].read_text()
    assert "AAPL" in content
    assert "84" in content
    assert "S2" in content


def test_execute_order_dry_run_appends_multiple_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.agents.trading_agent._RESULTS_DIR", tmp_path)
    agent = TradingAgent(dry_run=True)
    agent.execute_order({"symbol": "AAPL", "scenario_id": "S2", "market": "US"}, quantity=10)
    agent.execute_order({"symbol": "MSFT", "scenario_id": "S6", "market": "US"}, quantity=5)
    csvs = list(tmp_path.glob("signals_*.csv"))
    lines = csvs[0].read_text().splitlines()
    assert len(lines) == 3  # header + 2 rows


def test_execute_order_dry_run_skips_portfolio_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.agents.trading_agent._RESULTS_DIR", tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(
        "src.portfolio.manager.PortfolioManager.open_position",
        lambda *a, **kw: opened.append("called"),
    )
    agent = TradingAgent(dry_run=True)
    agent.execute_order({"symbol": "AAPL", "scenario_id": "S2", "market": "US"}, quantity=10)
    assert opened == []
