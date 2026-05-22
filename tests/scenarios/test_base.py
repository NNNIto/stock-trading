"""Tests for scenarios/base.py — abstract class, Position, helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from src.scenarios.base import (
    ExitReason,
    Position,
    ScenarioBase,
    ScenarioParams,
    _load_yaml,
    build_signal_row,
)

# ── Minimal concrete subclass for testing ─────────────────────────────────────


class _DummyParams(ScenarioParams):
    threshold: float = 0.05


class _DummyScenario(ScenarioBase):
    scenario_id = "DUMMY"

    def _parse_params(self, raw: dict[str, Any]) -> _DummyParams:
        return _DummyParams(**raw)

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": data["symbol"],
                "date": data["date"],
                "action": ["BUY"] * data.height,
                "scenario_id": ["DUMMY"] * data.height,
            }
        )

    def get_exit_signal(self, position: Position, current_data: dict[str, Any]) -> str:
        return ExitReason.NO_EXIT


@pytest.fixture
def dummy_config(tmp_path: Path) -> Path:
    cfg = {
        "scenario_id": "DUMMY",
        "name": "テスト用",
        "enabled": True,
        "version": "0.1.0",
        "threshold": 0.03,
    }
    p = tmp_path / "dummy.yaml"
    p.write_text(yaml.dump(cfg))
    return p


# ── Position ──────────────────────────────────────────────────────────────────


def test_position_default_peak_price():
    pos = Position("AAPL", "S2", date(2024, 1, 2), entry_price=100.0, quantity=10)
    assert pos.peak_price == 100.0


def test_position_explicit_peak():
    pos = Position("AAPL", "S2", date(2024, 1, 2), entry_price=100.0, quantity=10, peak_price=120.0)
    assert pos.peak_price == 120.0


def test_position_metadata():
    pos = Position(
        "AAPL",
        "S2",
        date(2024, 1, 2),
        entry_price=100.0,
        quantity=10,
        metadata={"earnings_date": "2024-04-30"},
    )
    assert pos.metadata["earnings_date"] == "2024-04-30"


# ── ExitReason ────────────────────────────────────────────────────────────────


def test_exit_reason_constants():
    assert ExitReason.STOP_LOSS == "stop_loss"
    assert ExitReason.TRAILING_STOP == "trailing_stop"
    assert ExitReason.TIME_EXIT == "time_exit"
    assert ExitReason.NO_EXIT == ""


# ── _load_yaml ────────────────────────────────────────────────────────────────


def test_load_yaml_ok(dummy_config: Path):
    data = _load_yaml(dummy_config)
    assert data["scenario_id"] == "DUMMY"
    assert data["threshold"] == 0.03


def test_load_yaml_missing_raises():
    with pytest.raises(FileNotFoundError):
        _load_yaml(Path("/nonexistent/path.yaml"))


# ── ScenarioBase (via _DummyScenario) ─────────────────────────────────────────


def test_scenario_loads_params(dummy_config: Path):
    s = _DummyScenario(config_path=dummy_config)
    assert s.params.scenario_id == "DUMMY"
    assert s.params.version == "0.1.0"
    assert s.is_enabled is True


def test_scenario_repr(dummy_config: Path):
    s = _DummyScenario(config_path=dummy_config)
    r = repr(s)
    assert "DUMMY" in r
    assert "0.1.0" in r


def test_generate_signals_returns_schema(dummy_config: Path):
    s = _DummyScenario(config_path=dummy_config)
    df = pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "close": [100.0, 101.0],
        }
    )
    result = s.generate_signals(df)
    assert "symbol" in result.columns
    assert "date" in result.columns
    assert "action" in result.columns
    assert "scenario_id" in result.columns


def test_get_exit_signal_no_exit(dummy_config: Path):
    s = _DummyScenario(config_path=dummy_config)
    pos = Position("AAPL", "DUMMY", date(2024, 1, 2), 100.0, 10)
    row: dict[str, Any] = {"close": 105.0}
    result = s.get_exit_signal(pos, row)
    assert result == ExitReason.NO_EXIT


# ── build_signal_row ──────────────────────────────────────────────────────────


def test_build_signal_row_buy():
    row = build_signal_row("AAPL", date(2024, 1, 2), "BUY", "S2", vol_ratio=1.8)
    assert row["symbol"] == "AAPL"
    assert row["action"] == "BUY"
    assert row["scenario_id"] == "S2"
    assert row["vol_ratio"] == 1.8


def test_build_signal_row_no_signal():
    row = build_signal_row("AAPL", date(2024, 1, 2), "", "S2")
    assert row["action"] == ""


# ── ScenarioParams Pydantic validation ────────────────────────────────────────


def test_scenario_params_extra_allowed():
    p = ScenarioParams(scenario_id="X", name="Test", custom_field=99)
    assert p.scenario_id == "X"


def test_scenario_params_enabled_default():
    p = ScenarioParams(scenario_id="X", name="Test")
    assert p.enabled is True


# ── Real YAML files exist and parse ───────────────────────────────────────────


@pytest.mark.parametrize("scenario_id", ["s2", "s3", "s4", "s6"])
def test_scenario_yaml_exists(scenario_id: str):
    from src.scenarios.base import _CONFIG_DIR

    path = _CONFIG_DIR / f"{scenario_id}.yaml"
    assert path.exists(), f"Missing: {path}"
    data = _load_yaml(path)
    assert "scenario_id" in data
    assert "parameters" in data
    assert "change_log" in data
