"""Tests for utils/config.py"""

from pathlib import Path

import pytest

from src.utils.config import Settings, load_settings


def test_load_settings_from_file():
    config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
    settings = load_settings(config_path)
    assert isinstance(settings, Settings)
    assert settings.project.name == "stock-trading"
    assert settings.execution.slippage_pct == pytest.approx(0.002)
    assert settings.risk.max_positions == 7
    assert settings.data.sources.primary == "yfinance"


def test_load_settings_defaults_when_file_missing(tmp_path):
    settings = load_settings(tmp_path / "nonexistent.yaml")
    assert isinstance(settings, Settings)
    assert settings.risk.max_positions == 7


def test_execution_config_values():
    settings = load_settings()
    assert settings.execution.commission_pct == pytest.approx(0.001)
    assert settings.execution.fx_cost_pct == pytest.approx(0.005)


def test_backtest_config():
    settings = load_settings()
    assert settings.backtest.random_seed == 42
    assert settings.backtest.learning_window_months == 12


def test_fallback_order():
    settings = load_settings()
    assert "stooq" in settings.data.sources.fallback_order
