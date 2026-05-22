"""Snapshot test: detect silent regressions in backtest output.

A deterministic small backtest is run and its key numeric outputs are
compared against stored reference values.  If a refactor changes the
trade count, final equity, or Sharpe ratio the test fails immediately.

To update snapshots after an intentional change:
    uv run pytest tests/backtest/test_snapshot.py --snapshot-update
(or delete _SNAPSHOT and re-run once to regenerate)
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.execution import ExecutionConfig
from src.backtest.metrics import compute_metrics
from src.portfolio.sizer import FixedFractionSizer
from src.scenarios.base import ExitReason, Position, ScenarioBase, ScenarioParams

# ── Snapshot file path ────────────────────────────────────────────────────────

_SNAPSHOT_PATH = Path(__file__).parent / "_snapshot_reference.json"


# ── Deterministic stub scenario ───────────────────────────────────────────────


class _DeterministicParams(ScenarioParams):
    stop_loss_pct: float = -0.05
    time_exit_days: int = 20


class _DeterministicScenario(ScenarioBase):
    """Buy every 7th row; exit on time_exit_days."""

    scenario_id = "S2"
    params: _DeterministicParams

    def __init__(self) -> None:
        self.params = _DeterministicParams(scenario_id="S2", name="det")

    def _parse_params(self, raw: dict[str, Any]) -> _DeterministicParams:
        return _DeterministicParams(scenario_id="S2", name="det")

    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        n = data.height
        actions = ["BUY" if i % 7 == 0 else "" for i in range(n)]
        return data.select(
            [
                pl.col("symbol"),
                pl.col("date"),
                pl.Series("action", actions).alias("action"),
                pl.lit("S2").alias("scenario_id"),
            ]
        )

    def get_exit_signal(self, position: Position, current_data: dict[str, Any]) -> str:
        close = float(current_data.get("close", position.entry_price))
        if close <= position.entry_price * (1 + self.params.stop_loss_pct):
            return ExitReason.STOP_LOSS
        if position.holding_days >= self.params.time_exit_days:
            return ExitReason.TIME_EXIT
        return ExitReason.NO_EXIT


def _make_deterministic_data() -> pl.DataFrame:
    """Fixed 365-day synthetic dataset with predictable price pattern."""
    start = date(2022, 1, 3)
    n = 365
    rows = []
    for i in range(n):
        # Slight uptrend with known cycle so signals fire consistently
        price = 100.0 + i * 0.05
        rows.append(
            {
                "symbol": "TEST",
                "date": start + timedelta(days=i),
                "open": price,
                "high": price * 1.005,
                "low": price * 0.995,
                "close": price,
                "adj_close": price,
                "volume": 1_000_000,
                "market": "JP",
                "ma_200": 100.0,
                "ma_200_slope": 0.1,
                "vol_ratio_20": 1.5,
                "high_252d": 115.0,
                "ma_50": 100.0,
                "ma_50_slope": 0.1,
                "ma_20": 100.0,
                "ma_20_slope": 0.1,
                "rsi_14": 55.0,
                "rsi_2": 55.0,
                "ret_5d": 0.002,
                "atr_14": 1.0,
                "vol_ma_20": 1_000_000.0,
            }
        )
    return pl.DataFrame(rows)


def _run_snapshot_backtest() -> dict[str, Any]:
    """Run the deterministic backtest and return key output values."""
    data = _make_deterministic_data()
    engine = BacktestEngine(
        scenarios=[_DeterministicScenario()],
        sizer=FixedFractionSizer(fraction=0.15),
        exec_config=ExecutionConfig(slippage_pct=0.001, commission_pct=0.001, fx_cost_pct=0.0),
        initial_capital=1_000_000,
        max_positions=7,
        random_seed=42,
    )
    start = date(2022, 1, 3)
    end = date(2022, 12, 31)
    result = engine.run(data, start, end)
    metrics = compute_metrics(result.trades, result.equity_curve, bootstrap_samples=0)

    # Compute a hash of the trade log for deeper regression detection
    if result.trades.is_empty():
        trades_hash = "empty"
    else:
        trade_bytes = (
            result.trades.select(["symbol", "entry_date", "exit_date", "quantity"])
            .write_csv()
            .encode()
        )
        trades_hash = hashlib.sha256(trade_bytes).hexdigest()[:16]

    final_equity = (
        float(result.equity_curve["portfolio_value"][-1]) if result.equity_curve.height > 0 else 0.0
    )

    return {
        "trade_count": metrics.trade_count,
        "final_equity": round(final_equity, 2),
        "sharpe_ratio": round(metrics.sharpe_ratio, 6),
        "max_drawdown": round(metrics.max_drawdown, 6),
        "win_rate": round(metrics.win_rate, 4),
        "trades_hash": trades_hash,
    }


# ── Snapshot management ───────────────────────────────────────────────────────


def _load_snapshot() -> dict[str, Any] | None:
    if _SNAPSHOT_PATH.exists():
        result: dict[str, Any] = json.loads(_SNAPSHOT_PATH.read_text())
        return result
    return None


def _save_snapshot(data: dict[str, Any]) -> None:
    _SNAPSHOT_PATH.write_text(json.dumps(data, indent=2))


# ── Test ──────────────────────────────────────────────────────────────────────


def test_backtest_snapshot(request: pytest.FixtureRequest) -> None:
    """Backtest output must match the stored snapshot.

    Pass --snapshot-update to regenerate the reference.
    """
    current = _run_snapshot_backtest()
    update = request.config.getoption("--snapshot-update", default=False)

    stored = _load_snapshot()

    if stored is None or update:
        _save_snapshot(current)
        if stored is None:
            pytest.skip("Snapshot created — re-run to verify")
        return  # updated intentionally

    mismatches = []
    for key, expected in stored.items():
        actual = current.get(key)
        if actual != expected:
            mismatches.append(f"  {key}: expected={expected!r} actual={actual!r}")

    if mismatches:
        pytest.fail(
            "Backtest snapshot mismatch — check for unintentional regressions:\n"
            + "\n".join(mismatches)
            + "\n\nTo intentionally update: uv run pytest tests/backtest/test_snapshot.py --snapshot-update"
        )


# ── pytest option registration ────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Regenerate snapshot references",
    )
