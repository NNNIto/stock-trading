"""Walk-forward analysis: rolling train/validate windows with per-scenario grid search.

Design
------
* In-sample (IS) period is split into overlapping windows:
    train  : learning_window_months months
    val    : validation_window_months months
    step   : step_months months

* For each window
    1. Grid search per scenario independently on the TRAINING slice
       (single-scenario engine to avoid cross-scenario interactions).
       Objective: maximise Sharpe ratio.
    2. Apply best params to all scenarios; run combined engine on VALIDATION slice.

* Out-of-sample period (settings.backtest.out_of_sample_start) is NEVER
  touched during walk-forward; it is reserved for final evaluation only.

* Degradation ratio = median(val_sharpe) / median(train_sharpe).
  Values below 0.5 indicate potential overfitting (scenarios.md 7.3).
"""

from __future__ import annotations

import calendar
import itertools
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl

from src.backtest.engine import BacktestEngine, MacroFilter
from src.backtest.execution import ExecutionConfig
from src.backtest.metrics import PerformanceMetrics, compute_metrics
from src.portfolio.sizer import PositionSizer
from src.scenarios.base import ScenarioBase
from src.utils.logger import get_logger

logger = get_logger()


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class WindowResult:
    """Result of a single walk-forward window."""

    window_id: int
    train_start: date
    train_end: date
    val_start: date
    val_end: date
    best_params: dict[str, dict[str, Any]]  # {scenario_id: {param_name: value}}
    train_metrics: PerformanceMetrics
    val_metrics: PerformanceMetrics


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward results across all windows."""

    windows: list[WindowResult]
    all_val_trades: pl.DataFrame  # validation trades concatenated across windows
    degradation_ratio: float  # median(val_sharpe) / median(train_sharpe)
    is_robust: bool  # degradation_ratio >= 0.5

    @property
    def median_train_sharpe(self) -> float:
        vals = [w.train_metrics.sharpe_ratio for w in self.windows]
        return statistics.median(vals) if vals else 0.0

    @property
    def median_val_sharpe(self) -> float:
        vals = [w.val_metrics.sharpe_ratio for w in self.windows]
        return statistics.median(vals) if vals else 0.0

    @property
    def n_windows(self) -> int:
        return len(self.windows)


# ── Main runner ───────────────────────────────────────────────────────────────


class WalkForwardRunner:
    """Orchestrates walk-forward analysis.

    Usage::

        runner = WalkForwardRunner(
            scenarios=[S2Breakout(), S3Pullback(), ...],
            sizer=FixedFractionSizer(0.15),
            exec_config=ExecutionConfig(),
            initial_capital=3_000_000,
        )
        result = runner.run(enriched_data, is_start=date(2018,1,1), is_end=date(2024,12,31))
    """

    def __init__(
        self,
        scenarios: list[ScenarioBase],
        sizer: PositionSizer,
        exec_config: ExecutionConfig,
        initial_capital: float,
        train_months: int = 12,
        val_months: int = 3,
        step_months: int = 3,
        max_positions: int = 7,
        random_seed: int = 42,
        optimize_metric: str = "sharpe_ratio",
        macro_filter: MacroFilter | None = None,
        degradation_threshold: float = 0.5,
    ) -> None:
        self.scenarios = scenarios
        self.sizer = sizer
        self.exec_config = exec_config
        self.initial_capital = initial_capital
        self.train_months = train_months
        self.val_months = val_months
        self.step_months = step_months
        self.max_positions = max_positions
        self.random_seed = random_seed
        self.optimize_metric = optimize_metric
        self.macro_filter = macro_filter
        self.degradation_threshold = degradation_threshold

    def run(
        self,
        data: pl.DataFrame,
        is_start: date,
        is_end: date,
    ) -> WalkForwardResult:
        """Execute walk-forward analysis over the in-sample period.

        Args:
            data:     Full indicator-enriched OHLCV DataFrame (all symbols, all dates).
                      Data outside [is_start, is_end] is ignored.
                      OOS period must NOT be passed here.
            is_start: First date of in-sample period (inclusive).
            is_end:   Last date of in-sample period (inclusive).
        """
        windows = _generate_windows(
            is_start, is_end, self.train_months, self.val_months, self.step_months
        )
        if not windows:
            raise ValueError(
                f"No walk-forward windows fit in [{is_start}, {is_end}] "
                f"with train={self.train_months}m + val={self.val_months}m"
            )

        logger.info(
            f"walkforward: {len(windows)} windows "
            f"({self.train_months}m train / {self.val_months}m val / {self.step_months}m step)"
        )

        all_val_trades_list: list[pl.DataFrame] = []
        window_results: list[WindowResult] = []

        for wid, (train_start, train_end, val_start, val_end) in enumerate(windows, start=1):
            logger.info(
                f"walkforward: window {wid}/{len(windows)} "
                f"train=[{train_start},{train_end}] val=[{val_start},{val_end}]"
            )

            train_data = data.filter(
                (pl.col("date") >= train_start) & (pl.col("date") <= train_end)
            )
            val_data = data.filter((pl.col("date") >= val_start) & (pl.col("date") <= val_end))

            # Grid search: per scenario, independent, on training data
            best_params = self._grid_search_all(train_data, train_start, train_end)

            # Apply best params for the validation run
            saved = self._apply_params(best_params)

            # Training metrics with best params
            train_result = self._build_engine().run(train_data, train_start, train_end)
            train_metrics = compute_metrics(
                train_result.trades,
                train_result.equity_curve,
                bootstrap_samples=200,
                random_seed=self.random_seed,
            )

            # Validation run with best params (all scenarios combined)
            val_result = self._build_engine().run(val_data, val_start, val_end)
            val_metrics = compute_metrics(
                val_result.trades,
                val_result.equity_curve,
                bootstrap_samples=200,
                random_seed=self.random_seed,
            )

            self._restore_params(saved)

            all_val_trades_list.append(val_result.trades)
            window_results.append(
                WindowResult(
                    window_id=wid,
                    train_start=train_start,
                    train_end=train_end,
                    val_start=val_start,
                    val_end=val_end,
                    best_params=best_params,
                    train_metrics=train_metrics,
                    val_metrics=val_metrics,
                )
            )

        all_val_trades = (
            pl.concat(all_val_trades_list, how="diagonal")
            if all_val_trades_list
            else pl.DataFrame()
        )

        train_sharpes = [w.train_metrics.sharpe_ratio for w in window_results]
        val_sharpes = [w.val_metrics.sharpe_ratio for w in window_results]
        med_train = statistics.median(train_sharpes) if train_sharpes else 0.0
        med_val = statistics.median(val_sharpes) if val_sharpes else 0.0
        degradation = (med_val / med_train) if med_train > 0 else 0.0

        return WalkForwardResult(
            windows=window_results,
            all_val_trades=all_val_trades,
            degradation_ratio=degradation,
            is_robust=degradation >= self.degradation_threshold,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_engine(self) -> BacktestEngine:
        return BacktestEngine(
            scenarios=self.scenarios,
            sizer=self.sizer,
            exec_config=self.exec_config,
            initial_capital=self.initial_capital,
            max_positions=self.max_positions,
            random_seed=self.random_seed,
            macro_filter=self.macro_filter,
        )

    def _grid_search_all(
        self, train_data: pl.DataFrame, train_start: date, train_end: date
    ) -> dict[str, dict[str, Any]]:
        """Run per-scenario grid search; return best param overrides."""
        best: dict[str, dict[str, Any]] = {}
        for scenario in self.scenarios:
            grid = _get_parameter_grid(scenario)
            if not grid:
                best[scenario.scenario_id] = {}
                continue
            best[scenario.scenario_id] = self._grid_search_one(
                scenario, grid, train_data, train_start, train_end
            )
        return best

    def _grid_search_one(
        self,
        scenario: ScenarioBase,
        grid: dict[str, list[Any]],
        train_data: pl.DataFrame,
        train_start: date,
        train_end: date,
    ) -> dict[str, Any]:
        """Grid search a single scenario; return the best param override dict."""
        keys = list(grid.keys())
        candidates = list(grid.values())
        best_score = float("-inf")
        best_override: dict[str, Any] = {}
        original_params = scenario.params

        # Single-scenario engine for isolated optimisation
        single_engine = BacktestEngine(
            scenarios=[scenario],
            sizer=self.sizer,
            exec_config=self.exec_config,
            initial_capital=self.initial_capital,
            max_positions=self.max_positions,
            random_seed=self.random_seed,
        )

        for combo in itertools.product(*candidates):
            override = dict(zip(keys, combo, strict=False))
            scenario.params = original_params.model_copy(update=override)
            try:
                result = single_engine.run(train_data, train_start, train_end)
                metrics = compute_metrics(
                    result.trades,
                    result.equity_curve,
                    bootstrap_samples=0,
                )
                score = float(getattr(metrics, self.optimize_metric, 0.0))
                if score > best_score:
                    best_score = score
                    best_override = override
            except Exception as exc:
                logger.debug(f"walkforward grid: {scenario.scenario_id} {override} → {exc}")
            finally:
                scenario.params = original_params

        logger.debug(
            f"walkforward: {scenario.scenario_id} best={best_override} score={best_score:.3f}"
        )
        return best_override

    def _apply_params(self, best: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Apply best params to scenarios; return saved originals for restore."""
        saved: dict[str, Any] = {}
        for scenario in self.scenarios:
            override = best.get(scenario.scenario_id, {})
            saved[scenario.scenario_id] = scenario.params
            if override:
                scenario.params = scenario.params.model_copy(update=override)
        return saved

    def _restore_params(self, saved: dict[str, Any]) -> None:
        for scenario in self.scenarios:
            orig = saved.get(scenario.scenario_id)
            if orig is not None:
                scenario.params = orig


# ── Date window helpers ───────────────────────────────────────────────────────


def _add_months(d: date, months: int) -> date:
    """Add a number of months to a date, clamping to end-of-month."""
    total_months = d.month - 1 + months
    year = d.year + total_months // 12
    month = total_months % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _generate_windows(
    is_start: date,
    is_end: date,
    train_months: int,
    val_months: int,
    step_months: int,
) -> list[tuple[date, date, date, date]]:
    """Generate (train_start, train_end, val_start, val_end) tuples."""
    windows: list[tuple[date, date, date, date]] = []
    train_start = is_start
    _one_day = timedelta(days=1)
    while True:
        train_end = _add_months(train_start, train_months) - _one_day
        val_start = _add_months(train_start, train_months)
        val_end = _add_months(val_start, val_months) - _one_day
        if val_end > is_end:
            break
        windows.append((train_start, train_end, val_start, val_end))
        train_start = _add_months(train_start, step_months)
    return windows


def _get_parameter_grid(scenario: ScenarioBase) -> dict[str, list[Any]]:
    """Read parameter_grid from scenario's raw YAML params."""
    raw = getattr(scenario, "_raw_params", {})
    if not isinstance(raw, dict):
        return {}
    grid = raw.get("parameter_grid", {})
    return {k: v for k, v in grid.items() if isinstance(v, list) and v}
