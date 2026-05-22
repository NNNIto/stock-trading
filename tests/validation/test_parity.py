"""Tests for L4 parity checker."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl

from src.scenarios.base import ExitReason, Position, ScenarioBase, ScenarioParams
from src.validation.parity_checker import ParityChecker


class _SimpleParams(ScenarioParams):
    pass


class _SimpleScenario(ScenarioBase):
    """BUY every 5th row — purely row-index based (no lookahead)."""

    scenario_id = "S2"
    params: _SimpleParams

    def __init__(self) -> None:
        self.params = _SimpleParams(scenario_id="S2", name="simple")

    def _parse_params(self, raw: dict) -> _SimpleParams:  # type: ignore[override]
        return _SimpleParams(scenario_id="S2", name="simple")

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        n = data.height
        actions = ["BUY" if i % 5 == 0 else "" for i in range(n)]
        return data.select(
            [
                pl.col("symbol"),
                pl.col("date"),
                pl.Series("action", actions).alias("action"),
                pl.lit("S2").alias("scenario_id"),
            ]
        )

    def get_exit_signal(self, pos: Position, d: dict[str, Any]) -> str:
        return ExitReason.NO_EXIT


def _make_data(n: int = 30) -> pl.DataFrame:
    start = date(2023, 1, 2)
    prices = [100.0 + i * 0.1 for i in range(n)]
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


def test_parity_passes_for_clean_scenario():
    data = _make_data(30)
    checker = ParityChecker([_SimpleScenario()], raise_on_mismatch=True)
    start = date(2023, 1, 10)
    end = date(2023, 1, 25)
    report = checker.check(data, start, end)
    assert report.passed


def test_parity_check_recent():
    data = _make_data(30)
    checker = ParityChecker([_SimpleScenario()], raise_on_mismatch=False)
    report = checker.check_recent(data, lookback_days=10)
    assert report.passed


def test_parity_empty_data_returns_empty_report():
    empty = pl.DataFrame(
        {
            "symbol": pl.Series([], dtype=pl.Utf8),
            "date": pl.Series([], dtype=pl.Date),
            "close": pl.Series([], dtype=pl.Float64),
            "open": pl.Series([], dtype=pl.Float64),
            "high": pl.Series([], dtype=pl.Float64),
            "low": pl.Series([], dtype=pl.Float64),
            "adj_close": pl.Series([], dtype=pl.Float64),
            "volume": pl.Series([], dtype=pl.Int64),
            "market": pl.Series([], dtype=pl.Utf8),
        }
    )
    checker = ParityChecker([_SimpleScenario()], raise_on_mismatch=False)
    report = checker.check_recent(empty, lookback_days=10)
    assert report.total_checked == 0
