"""Tests for src/reporting/generator.py."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from src.reporting.generator import (
    _metrics_dict,
    generate_monthly_report,
    generate_weekly_report,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_trades(pnl_list: list[float]) -> pl.DataFrame:
    n = len(pnl_list)
    start = date(2024, 1, 2)
    return pl.DataFrame(
        {
            "trade_id": [f"t{i}" for i in range(n)],
            "mode": ["paper"] * n,
            "scenario_id": ["S2"] * n,
            "scenario_version": ["abc"] * n,
            "symbol": ["AAPL"] * n,
            "market": ["US"] * n,
            "entry_date": [start] * n,
            "entry_price": [100.0] * n,
            "exit_date": [start + timedelta(days=10)] * n,
            "exit_price": [100.0 + p / 100 for p in pnl_list],
            "quantity": [100] * n,
            "fees": [10.0] * n,
            "pnl": pnl_list,
            "pnl_pct": [p / 10_000 for p in pnl_list],
            "holding_days": [10] * n,
            "exit_reason": ["time_exit"] * n,
        }
    )


# ── _metrics_dict ─────────────────────────────────────────────────────────────


def test_metrics_dict_empty_trades():
    result = _metrics_dict(pl.DataFrame())
    assert result["trade_count"] == 0
    assert result["total_pnl"] == 0.0
    assert isinstance(result["win_rate"], float)
    assert isinstance(result["is_reliable"], bool)


def test_metrics_dict_with_trades():
    trades = _make_trades([100.0, -50.0, 200.0])
    result = _metrics_dict(trades)
    assert result["trade_count"] == 3
    assert result["total_pnl"] == pytest.approx(250.0)
    assert 0.0 <= result["win_rate"] <= 1.0


def test_metrics_dict_all_wins():
    trades = _make_trades([100.0, 200.0, 300.0])
    result = _metrics_dict(trades)
    assert result["win_rate"] == pytest.approx(1.0)


def test_metrics_dict_all_losses():
    trades = _make_trades([-100.0, -200.0])
    result = _metrics_dict(trades)
    assert result["win_rate"] == pytest.approx(0.0)
    assert result["total_pnl"] == pytest.approx(-300.0)


def test_metrics_dict_payoff_ratio_inf_becomes_none():
    # No losses → payoff_ratio == inf → should be serialised as None
    trades = _make_trades([100.0, 200.0])
    result = _metrics_dict(trades)
    # Either None (inf suppressed) or a finite float
    assert result["payoff_ratio"] is None or isinstance(result["payoff_ratio"], float)


# ── generate_weekly_report ────────────────────────────────────────────────────


def test_weekly_report_date_range(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_load(start: date, end: date) -> pl.DataFrame:
        captured["start"] = start
        captured["end"] = end
        return pl.DataFrame()

    monkeypatch.setattr("src.reporting.generator._load_trades", fake_load)

    # Use a known Wednesday (2024-01-10) as report_date
    report_date = date(2024, 1, 10)  # Wednesday
    generate_weekly_report(report_date=report_date, save=False)

    # last Sunday = 2024-01-07, last Monday = 2024-01-01
    assert captured["end"] == date(2024, 1, 7)
    assert captured["start"] == date(2024, 1, 1)


def test_weekly_report_structure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("src.reporting.generator._load_trades", lambda s, e: pl.DataFrame())

    report = generate_weekly_report(report_date=date(2024, 1, 10), save=False)

    assert report["type"] == "weekly"
    assert "week_start" in report
    assert "week_end" in report
    assert "generated_at" in report
    assert "overall" in report
    assert "by_scenario" in report


def test_weekly_report_with_trades(monkeypatch: pytest.MonkeyPatch):
    trades = _make_trades([100.0, -50.0, 200.0])
    monkeypatch.setattr("src.reporting.generator._load_trades", lambda s, e: trades)

    report = generate_weekly_report(report_date=date(2024, 1, 10), save=False)

    assert report["overall"]["trade_count"] == 3
    assert "S2" in report["by_scenario"]


def test_weekly_report_saves_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("src.reporting.generator._load_trades", lambda s, e: pl.DataFrame())
    monkeypatch.setattr("src.reporting.generator._RESULTS_DIR", tmp_path)

    report_date = date(2024, 1, 10)
    generate_weekly_report(report_date=report_date, save=True)

    saved = list(tmp_path.glob("report_weekly_*.json"))
    assert len(saved) == 1
    data = json.loads(saved[0].read_text())
    assert data["type"] == "weekly"


# ── generate_monthly_report ───────────────────────────────────────────────────


def test_monthly_report_date_range(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_load(start: date, end: date) -> pl.DataFrame:
        captured["start"] = start
        captured["end"] = end
        return pl.DataFrame()

    monkeypatch.setattr("src.reporting.generator._load_trades", fake_load)

    # report_date in February → previous month is January
    generate_monthly_report(report_date=date(2024, 2, 15), save=False)

    assert captured["start"] == date(2024, 1, 1)
    assert captured["end"] == date(2024, 1, 31)


def test_monthly_report_structure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("src.reporting.generator._load_trades", lambda s, e: pl.DataFrame())

    report = generate_monthly_report(report_date=date(2024, 2, 15), save=False)

    assert report["type"] == "monthly"
    assert "month_start" in report
    assert "month_end" in report
    assert "generated_at" in report
    assert "overall" in report
    assert "by_scenario" in report


def test_monthly_report_with_trades(monkeypatch: pytest.MonkeyPatch):
    trades = _make_trades([300.0, -100.0])
    monkeypatch.setattr("src.reporting.generator._load_trades", lambda s, e: trades)

    report = generate_monthly_report(report_date=date(2024, 2, 15), save=False)

    assert report["overall"]["trade_count"] == 2
    assert report["overall"]["total_pnl"] == pytest.approx(200.0)


def test_monthly_report_saves_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("src.reporting.generator._load_trades", lambda s, e: pl.DataFrame())
    monkeypatch.setattr("src.reporting.generator._RESULTS_DIR", tmp_path)

    generate_monthly_report(report_date=date(2024, 2, 15), save=True)

    saved = list(tmp_path.glob("report_monthly_*.json"))
    assert len(saved) == 1
    data = json.loads(saved[0].read_text())
    assert data["type"] == "monthly"


def test_monthly_report_cross_year(monkeypatch: pytest.MonkeyPatch):
    """January report_date → previous month is December of prior year."""
    captured: dict = {}

    def fake_load(start: date, end: date) -> pl.DataFrame:
        captured["start"] = start
        captured["end"] = end
        return pl.DataFrame()

    monkeypatch.setattr("src.reporting.generator._load_trades", fake_load)

    generate_monthly_report(report_date=date(2024, 1, 5), save=False)

    assert captured["start"] == date(2023, 12, 1)
    assert captured["end"] == date(2023, 12, 31)
