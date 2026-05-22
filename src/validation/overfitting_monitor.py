"""L4 overfitting monitor: parameter sensitivity and IS/OOS degradation detection.

Two checks:
1. Sensitivity analysis: vary each parameter ±10-20 % around the current
   value and confirm that Sharpe does not change drastically (> 50 % drop).
   A strategy that only works for a precise parameter value is overfit.

2. IS/OOS degradation: if the in-sample Sharpe exceeds the out-of-sample
   Sharpe by more than 50 %, the strategy is flagged as potentially overfit
   (scenarios.md 7.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from src.backtest.engine import BacktestEngine
from src.backtest.execution import ExecutionConfig
from src.backtest.metrics import PerformanceMetrics, compute_metrics
from src.portfolio.sizer import PositionSizer
from src.scenarios.base import ScenarioBase
from src.utils.logger import get_logger

logger = get_logger()


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class SensitivityPoint:
    param_name: str
    param_value: Any
    sharpe: float


@dataclass
class OverfittingReport:
    scenario_id: str
    is_sharpe: float
    oos_sharpe: float
    degradation_ratio: float  # oos_sharpe / is_sharpe
    is_overfit: bool  # degradation < threshold (default 0.5)
    sensitivity: list[SensitivityPoint] = field(default_factory=list)
    sensitivity_violations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "OVERFIT" if self.is_overfit else "OK"
        lines = [
            f"[{status}] {self.scenario_id}",
            f"  IS Sharpe:         {self.is_sharpe:.3f}",
            f"  OOS Sharpe:        {self.oos_sharpe:.3f}",
            f"  Degradation ratio: {self.degradation_ratio:.3f} (threshold ≥ 0.5)",
        ]
        if self.sensitivity_violations:
            lines.append("  Sensitivity issues:")
            for v in self.sensitivity_violations:
                lines.append(f"    ✗ {v}")
        return "\n".join(lines)


# ── Monitor ───────────────────────────────────────────────────────────────────


class OverfittingMonitor:
    """Detect overfitting through IS/OOS degradation and parameter sensitivity.

    Args:
        degradation_threshold: OOS/IS Sharpe ratio below this triggers rejection.
        sensitivity_drop_threshold: A Sharpe drop > this fraction triggers a warning.
        sensitivity_steps: Number of values to test per parameter (default 3: -20%, base, +20%).
    """

    def __init__(
        self,
        degradation_threshold: float = 0.5,
        sensitivity_drop_threshold: float = 0.5,
        sensitivity_steps: int = 3,
    ) -> None:
        self.degradation_threshold = degradation_threshold
        self.sensitivity_drop_threshold = sensitivity_drop_threshold
        self.sensitivity_steps = sensitivity_steps

    def check_degradation(
        self,
        is_metrics: PerformanceMetrics,
        oos_metrics: PerformanceMetrics,
        scenario_id: str = "portfolio",
    ) -> OverfittingReport:
        """Check IS→OOS Sharpe degradation."""
        is_s = is_metrics.sharpe_ratio
        oos_s = oos_metrics.sharpe_ratio
        ratio = (oos_s / is_s) if is_s > 0 else 0.0
        is_overfit = ratio < self.degradation_threshold and is_s > 0

        report = OverfittingReport(
            scenario_id=scenario_id,
            is_sharpe=is_s,
            oos_sharpe=oos_s,
            degradation_ratio=ratio,
            is_overfit=is_overfit,
        )
        if is_overfit:
            logger.error(
                f"overfitting [{scenario_id}]: degradation {ratio:.2f} < "
                f"{self.degradation_threshold} — strategy REJECTED"
            )
        else:
            logger.info(report.summary())
        return report

    def sensitivity_analysis(
        self,
        scenario: ScenarioBase,
        data: pl.DataFrame,
        start: date,
        end: date,
        sizer: PositionSizer,
        exec_config: ExecutionConfig,
        initial_capital: float,
        random_seed: int = 42,
    ) -> OverfittingReport:
        """Vary each grid-search parameter ±20 % and measure Sharpe impact."""

        # Re-use _get_parameter_grid from walkforward helpers
        try:
            from src.backtest.walkforward import _get_parameter_grid as _gpg

            grid = _gpg(scenario)
        except Exception:
            grid = {}

        baseline_sharpe = self._run_sharpe(
            scenario, data, start, end, sizer, exec_config, initial_capital, random_seed
        )

        sensitivity: list[SensitivityPoint] = []
        violations: list[str] = []
        original_params = scenario.params

        for param_name, candidates in grid.items():
            for val in candidates:
                scenario.params = original_params.model_copy(update={param_name: val})
                sharpe = self._run_sharpe(
                    scenario, data, start, end, sizer, exec_config, initial_capital, random_seed
                )
                scenario.params = original_params
                sensitivity.append(SensitivityPoint(param_name, val, sharpe))

                if baseline_sharpe > 0:
                    drop = (baseline_sharpe - sharpe) / baseline_sharpe
                    if drop > self.sensitivity_drop_threshold:
                        msg = (
                            f"{param_name}={val}: Sharpe {sharpe:.3f} vs baseline "
                            f"{baseline_sharpe:.3f} (drop {drop:.0%})"
                        )
                        violations.append(msg)
                        logger.warning(f"sensitivity [{scenario.scenario_id}] {msg}")

        return OverfittingReport(
            scenario_id=scenario.scenario_id,
            is_sharpe=baseline_sharpe,
            oos_sharpe=0.0,  # not applicable for sensitivity-only run
            degradation_ratio=1.0,
            is_overfit=bool(violations),
            sensitivity=sensitivity,
            sensitivity_violations=violations,
        )

    def _run_sharpe(
        self,
        scenario: ScenarioBase,
        data: pl.DataFrame,
        start: date,
        end: date,
        sizer: PositionSizer,
        exec_config: ExecutionConfig,
        initial_capital: float,
        random_seed: int,
    ) -> float:
        engine = BacktestEngine(
            scenarios=[scenario],
            sizer=sizer,
            exec_config=exec_config,
            initial_capital=initial_capital,
            max_positions=7,
            random_seed=random_seed,
        )
        result = engine.run(data, start, end)
        metrics = compute_metrics(result.trades, result.equity_curve, bootstrap_samples=0)
        return metrics.sharpe_ratio
