"""Tests for L3 lookahead bias detection."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from src.scenarios.base import ExitReason, Position, ScenarioBase, ScenarioParams
from src.validation.lookahead_detector import (
    LookaheadBiasError,
    LookaheadDetector,
)


class _CleanParams(ScenarioParams):
    pass


class _CleanScenario(ScenarioBase):
    """No lookahead: BUY when close > shift(1) close."""

    scenario_id = "S2"
    params: _CleanParams

    def __init__(self) -> None:
        self.params = _CleanParams(scenario_id="S2", name="clean")

    def _parse_params(self, raw: dict) -> _CleanParams:  # type: ignore[override]
        return _CleanParams(scenario_id="S2", name="clean")

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        signal = pl.col("close") > pl.col("close").shift(1)
        return data.select(
            [
                pl.col("symbol"),
                pl.col("date"),
                pl.when(signal).then(pl.lit("BUY")).otherwise(pl.lit("")).alias("action"),
                pl.lit("S2").alias("scenario_id"),
            ]
        )

    def get_exit_signal(self, pos: Position, d: dict[str, Any]) -> str:
        return ExitReason.NO_EXIT


class _BuggyScenario(ScenarioBase):
    """Lookahead: uses NEXT row's close (shift(-1) = future)."""

    scenario_id = "S2"
    params: _CleanParams

    def __init__(self) -> None:
        self.params = _CleanParams(scenario_id="S2", name="buggy")

    def _parse_params(self, raw: dict) -> _CleanParams:  # type: ignore[override]
        return _CleanParams(scenario_id="S2", name="buggy")

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        signal = pl.col("close").shift(-1) > pl.col("close")  # BUG: reads future
        return data.select(
            [
                pl.col("symbol"),
                pl.col("date"),
                pl.when(signal).then(pl.lit("BUY")).otherwise(pl.lit("")).alias("action"),
                pl.lit("S2").alias("scenario_id"),
            ]
        )

    def get_exit_signal(self, pos: Position, d: dict[str, Any]) -> str:
        return ExitReason.NO_EXIT


def _make_data() -> pl.DataFrame:
    start = date(2022, 1, 3)
    prices = [100.0, 101.0, 100.5, 102.0, 101.5, 103.0, 102.5, 104.0] * 5
    n = len(prices)
    return pl.DataFrame(
        {
            "symbol": ["AAPL"] * n,
            "date": [start + timedelta(days=i) for i in range(n)],
            "close": prices,
            "open": prices,
            "high": prices,
            "low": prices,
            "adj_close": prices,
            "volume": [1_000_000] * n,
            "market": ["US"] * n,
        }
    )


def test_clean_scenario_passes():
    data = _make_data()
    detector = LookaheadDetector(raise_on_violation=True)
    report = detector.check(_CleanScenario(), data)
    assert report.passed


def test_buggy_scenario_detected():
    data = _make_data()
    detector = LookaheadDetector(raise_on_violation=True)
    with pytest.raises(LookaheadBiasError):
        detector.check(_BuggyScenario(), data)


def test_no_raise_collects_all_violations():
    data = _make_data()
    detector = LookaheadDetector(raise_on_violation=False)
    report = detector.check(_BuggyScenario(), data)
    assert not report.passed
    assert len(report.violations) > 0


def test_check_all_returns_per_scenario_reports():
    data = _make_data()
    detector = LookaheadDetector(raise_on_violation=False)
    reports = detector.check_all([_CleanScenario(), _BuggyScenario()], data)
    assert len(reports) == 2
    assert reports[0].passed
    assert not reports[1].passed
