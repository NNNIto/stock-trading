"""Tests for L4 sanity checker and overfitting monitor."""

from __future__ import annotations

from typing import Any

import pytest

from src.backtest.metrics import PerformanceMetrics
from src.validation.overfitting_monitor import OverfittingMonitor
from src.validation.sanity_checker import run_sanity_checks


def _metrics(**overrides: Any) -> PerformanceMetrics:
    defaults: dict[str, Any] = dict(
        total_return_pct=0.15,
        cagr=0.15,
        sharpe_ratio=1.2,
        sortino_ratio=1.5,
        max_drawdown=-0.08,
        avg_drawdown=-0.03,
        trade_count=40,
        win_rate=0.55,
        payoff_ratio=1.8,
        profit_factor=2.2,
        avg_holding_days=15.0,
        is_reliable=True,
        sharpe_ci_low=0.9,
        sharpe_ci_high=1.5,
    )
    defaults.update(overrides)
    return PerformanceMetrics(**defaults)


# ── Sanity checker ────────────────────────────────────────────────────────────


def test_good_metrics_pass():
    m = _metrics()
    report = run_sanity_checks(m, "S2")
    assert report.passed


def test_too_few_trades_error():
    m = _metrics(trade_count=10)
    report = run_sanity_checks(m, "S2")
    assert not report.passed
    assert any(i.check_name == "trade_count" for i in report.issues)


def test_sharpe_too_high_error():
    m = _metrics(sharpe_ratio=3.5, trade_count=50)
    report = run_sanity_checks(m, "S2")
    assert not report.passed
    assert any(i.check_name == "sharpe_too_high" for i in report.issues)


def test_sharpe_elevated_warning():
    m = _metrics(sharpe_ratio=2.5, trade_count=50)
    report = run_sanity_checks(m, "S2")
    assert report.passed  # warning, not error
    assert report.has_warnings


def test_win_rate_too_high_error():
    m = _metrics(win_rate=0.90, trade_count=50)
    report = run_sanity_checks(m, "S2")
    assert not report.passed
    assert any(i.check_name == "win_rate_too_high" for i in report.issues)


def test_zero_drawdown_warning():
    m = _metrics(max_drawdown=0.0, trade_count=50)
    report = run_sanity_checks(m, "S2")
    assert report.has_warnings


def test_extreme_drawdown_error():
    m = _metrics(max_drawdown=-0.60)
    report = run_sanity_checks(m, "S2")
    assert not report.passed
    assert any(i.check_name == "extreme_drawdown" for i in report.issues)


def test_zero_holding_days_error():
    m = _metrics(avg_holding_days=0.0, trade_count=50)
    report = run_sanity_checks(m, "S2")
    assert not report.passed


# ── Overfitting monitor ───────────────────────────────────────────────────────


def test_degradation_ok():
    monitor = OverfittingMonitor(degradation_threshold=0.5)
    is_m = _metrics(sharpe_ratio=1.5)
    oos_m = _metrics(sharpe_ratio=1.0)
    report = monitor.check_degradation(is_m, oos_m, "S2")
    assert not report.is_overfit
    assert report.degradation_ratio == pytest.approx(1.0 / 1.5, rel=0.01)


def test_degradation_triggers():
    monitor = OverfittingMonitor(degradation_threshold=0.5)
    is_m = _metrics(sharpe_ratio=2.0)
    oos_m = _metrics(sharpe_ratio=0.5)  # ratio = 0.25 < 0.5
    report = monitor.check_degradation(is_m, oos_m, "S2")
    assert report.is_overfit


def test_degradation_zero_is_sharpe():
    monitor = OverfittingMonitor()
    is_m = _metrics(sharpe_ratio=0.0)
    oos_m = _metrics(sharpe_ratio=-0.5)
    report = monitor.check_degradation(is_m, oos_m, "S2")
    assert not report.is_overfit  # ratio=0 when is_sharpe=0 → not flagged
