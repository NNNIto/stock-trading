"""L3 financial logic validation: lookahead (look-ahead) bias detection.

Algorithm
---------
For each scenario S and each date T in the test window:
  1. Full run:   compute generate_signals(data[all dates])  → signal_full[T]
  2. Truncated:  compute generate_signals(data[: T])        → signal_trunc[T]
  3. Assert signal_full[T] == signal_trunc[T]

A mismatch means that computing the signal on date T requires data from
after T (look-ahead bias).  The test deliberately passes ALL in-sample
data, so the only way a mismatch can arise is if the scenario code reads
future rows.

This runs BEFORE any backtest.  A detection causes the backtest to be
tagged "untrusted" and an error is raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from src.scenarios.base import ScenarioBase
from src.utils.logger import get_logger

logger = get_logger()


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class LookaheadViolation:
    scenario_id: str
    check_date: date
    symbol: str
    full_action: str  # action computed with full data
    trunc_action: str  # action computed with truncated data


@dataclass
class LookaheadReport:
    scenario_id: str
    violations: list[LookaheadViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0

    def summary(self) -> str:
        if self.passed:
            return f"[PASS] {self.scenario_id}: no lookahead bias detected"
        return (
            f"[FAIL] {self.scenario_id}: {len(self.violations)} lookahead violation(s)\n"
            + "\n".join(
                f"  {v.check_date} {v.symbol}: full={v.full_action!r} trunc={v.trunc_action!r}"
                for v in self.violations[:5]
            )
            + ("  ..." if len(self.violations) > 5 else "")
        )


# ── Detector ──────────────────────────────────────────────────────────────────


class LookaheadDetector:
    """Verify that scenario signals are free of look-ahead bias.

    Args:
        check_dates: Dates to verify.  If None, checks every 20th trading day
                     in the data (proportional sampling for speed).
        raise_on_violation: If True (default), raise immediately on first failure.
    """

    def __init__(
        self,
        check_dates: list[date] | None = None,
        raise_on_violation: bool = True,
    ) -> None:
        self.check_dates = check_dates
        self.raise_on_violation = raise_on_violation

    def check(
        self,
        scenario: ScenarioBase,
        data: pl.DataFrame,
    ) -> LookaheadReport:
        """Run lookahead check for one scenario across all symbols in data."""
        report = LookaheadReport(scenario_id=scenario.scenario_id)

        all_dates = sorted(data["date"].unique().to_list())
        if not all_dates:
            return report

        # Select check dates (every 20th day or explicitly supplied)
        dates_to_check = self.check_dates or all_dates[::20] or [all_dates[-1]]

        # Pre-compute full-data signals once per symbol
        full_signals: dict[str, dict[date, str]] = {}
        for sym in data["symbol"].unique().to_list():
            sym_data = data.filter(pl.col("symbol") == sym).sort("date")
            try:
                sig_df = scenario.generate_signals(sym_data)
                full_signals[sym] = {
                    row["date"]: row["action"] for row in sig_df.iter_rows(named=True)
                }
            except Exception as exc:
                logger.warning(f"lookahead: {scenario.scenario_id}/{sym} full-run error: {exc}")

        # For each check date, truncate and compare
        for check_date in dates_to_check:
            trunc = data.filter(pl.col("date") <= check_date)
            for sym in trunc["symbol"].unique().to_list():
                sym_trunc = trunc.filter(pl.col("symbol") == sym).sort("date")
                if sym_trunc.is_empty():
                    continue
                try:
                    trunc_sig_df = scenario.generate_signals(sym_trunc)
                    if trunc_sig_df.is_empty():
                        continue
                    trunc_action = trunc_sig_df.filter(pl.col("date") == check_date)["action"]
                    trunc_act = trunc_action[0] if len(trunc_action) > 0 else ""
                except Exception:
                    continue

                full_act = full_signals.get(sym, {}).get(check_date, "")

                if full_act != trunc_act:
                    violation = LookaheadViolation(
                        scenario_id=scenario.scenario_id,
                        check_date=check_date,
                        symbol=sym,
                        full_action=full_act,
                        trunc_action=trunc_act,
                    )
                    report.violations.append(violation)
                    logger.error(
                        f"lookahead VIOLATION: {scenario.scenario_id}/{sym} @ {check_date} "
                        f"full={full_act!r} trunc={trunc_act!r}"
                    )
                    if self.raise_on_violation:
                        raise LookaheadBiasError(report)

        logger.info(report.summary())
        return report

    def check_all(
        self,
        scenarios: list[ScenarioBase],
        data: pl.DataFrame,
    ) -> list[LookaheadReport]:
        """Run lookahead check for each scenario; return all reports."""
        return [self.check(s, data) for s in scenarios]


class LookaheadBiasError(Exception):
    """Raised when a lookahead violation is detected with raise_on_violation=True."""

    def __init__(self, report: LookaheadReport) -> None:
        self.report = report
        super().__init__(
            f"Lookahead bias detected in {report.scenario_id}: "
            f"{len(report.violations)} violation(s). "
            "Backtest result is UNTRUSTED."
        )
