"""Tests for RiskManager."""

from __future__ import annotations

import pytest

from src.portfolio.risk import RiskManager


@pytest.fixture
def rm() -> RiskManager:
    return RiskManager(max_positions=7, max_sector_positions=3, portfolio_dd_threshold=-0.20)


def test_circuit_breaker_blocks_at_threshold(rm: RiskManager) -> None:
    result = rm.check_circuit_breaker(800_000, 1_000_000)  # -20%
    assert not result.allowed


def test_circuit_breaker_allows_below_threshold(rm: RiskManager) -> None:
    result = rm.check_circuit_breaker(850_000, 1_000_000)  # -15%
    assert result.allowed


def test_circuit_breaker_zero_peak(rm: RiskManager) -> None:
    result = rm.check_circuit_breaker(0, 0)
    assert result.allowed  # no peak data → allow


def test_macro_blocks_high_vix(rm: RiskManager) -> None:
    result = rm.check_macro(vix=36.0)
    assert not result.allowed


def test_macro_allows_normal_vix(rm: RiskManager) -> None:
    result = rm.check_macro(vix=20.0)
    assert result.allowed


def test_macro_blocks_nikkei_crash(rm: RiskManager) -> None:
    result = rm.check_macro(nikkei_daily_return=-0.11)
    assert not result.allowed


def test_position_limit_blocks_at_max(rm: RiskManager) -> None:
    assert not rm.check_position_limit(7).allowed


def test_position_limit_allows_below_max(rm: RiskManager) -> None:
    assert rm.check_position_limit(6).allowed


def test_sector_blocks_at_max(rm: RiskManager) -> None:
    counts = {"Tech": 3}
    assert not rm.check_sector_concentration("Tech", counts).allowed


def test_sector_allows_unknown(rm: RiskManager) -> None:
    assert rm.check_sector_concentration(None, {}).allowed


def test_assess_new_entry_all_clear(rm: RiskManager) -> None:
    result = rm.assess_new_entry(
        "AAPL",
        "Tech",
        n_open=3,
        open_sector_counts={"Tech": 1},
        portfolio_value=1_000_000,
        peak_value=1_000_000,
    )
    assert result.allowed


def test_assess_new_entry_circuit_breaker(rm: RiskManager) -> None:
    result = rm.assess_new_entry(
        "AAPL",
        "Tech",
        n_open=1,
        open_sector_counts={},
        portfolio_value=700_000,
        peak_value=1_000_000,  # -30%
    )
    assert not result.allowed
