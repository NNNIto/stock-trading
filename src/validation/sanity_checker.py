"""L4 backtest sanity checks: detect suspicious metrics that indicate overfitting.

Checks (any failure marks the result as SUSPECT):
  - Trade count >= 30 (statistical reliability)
  - Sharpe ratio <= 3.0 (> 3.0 is almost always curve-fitted or a data error)
  - Win rate <= 80 %  (> 80 % with many trades is highly suspect)
  - Max drawdown < 0  (a flat or positive drawdown means no risk was taken)
  - Max drawdown > -50 % (extreme loss)
  - Avg holding days > 0
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.backtest.metrics import PerformanceMetrics
from src.utils.logger import get_logger

logger = get_logger()


@dataclass
class SanityIssue:
    check_name: str
    message: str
    severity: str  # 'warning' | 'error'


@dataclass
class SanityReport:
    scenario_id: str
    issues: list[SanityIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    def summary(self) -> str:
        if not self.issues:
            return f"[PASS] {self.scenario_id}: all sanity checks passed"
        lines = [f"[{'PASS' if self.passed else 'FAIL'}] {self.scenario_id}:"]
        for issue in self.issues:
            tag = "⚠ " if issue.severity == "warning" else "✗ "
            lines.append(f"  {tag}{issue.check_name}: {issue.message}")
        return "\n".join(lines)


def run_sanity_checks(
    metrics: PerformanceMetrics,
    scenario_id: str = "portfolio",
    min_trades: int = 30,
    max_sharpe: float = 3.0,
    max_win_rate: float = 0.80,
) -> SanityReport:
    """Run all sanity checks on a PerformanceMetrics object.

    Returns a SanityReport; call .passed to check if the result is trustworthy.
    """
    report = SanityReport(scenario_id=scenario_id)

    def _warn(name: str, msg: str) -> None:
        report.issues.append(SanityIssue(name, msg, "warning"))
        logger.warning(f"sanity [{scenario_id}] {name}: {msg}")

    def _error(name: str, msg: str) -> None:
        report.issues.append(SanityIssue(name, msg, "error"))
        logger.error(f"sanity [{scenario_id}] {name}: {msg}")

    # ── Trade count ───────────────────────────────────────────────────────────
    if metrics.trade_count < min_trades:
        _error(
            "trade_count",
            f"{metrics.trade_count} trades < {min_trades} — statistically unreliable",
        )

    # ── Sharpe ratio ──────────────────────────────────────────────────────────
    if metrics.sharpe_ratio > max_sharpe:
        _error(
            "sharpe_too_high",
            f"Sharpe {metrics.sharpe_ratio:.2f} > {max_sharpe} — likely overfitted or data error",
        )
    elif metrics.sharpe_ratio > 2.0:
        _warn(
            "sharpe_elevated",
            f"Sharpe {metrics.sharpe_ratio:.2f} > 2.0 — verify robustness with walk-forward",
        )

    # ── Win rate ──────────────────────────────────────────────────────────────
    if metrics.trade_count >= min_trades and metrics.win_rate > max_win_rate:
        _error(
            "win_rate_too_high",
            f"Win rate {metrics.win_rate:.1%} > {max_win_rate:.0%} — suspect with {metrics.trade_count} trades",
        )

    # ── Drawdown ──────────────────────────────────────────────────────────────
    if metrics.max_drawdown >= 0.0 and metrics.trade_count >= min_trades:
        _warn(
            "zero_drawdown",
            "Max drawdown = 0 — position sizing or data may be incorrect",
        )
    if metrics.max_drawdown < -0.50:
        _error(
            "extreme_drawdown",
            f"Max drawdown {metrics.max_drawdown:.1%} < -50 % — catastrophic loss",
        )

    # ── Avg holding days ─────────────────────────────────────────────────────
    if metrics.trade_count > 0 and metrics.avg_holding_days <= 0:
        _error("holding_days", "Avg holding days <= 0 — entry/exit logic error")

    logger.info(report.summary())
    return report
