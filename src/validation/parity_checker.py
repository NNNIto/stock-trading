"""L4 バックテスト・ライブ一貫性検証 (parity checker).

バックテストエンジンが生成したシグナルと、daily_signals.py を各日の
データ断面で再実行した結果を銘柄・日付・アクション単位で突き合わせる。

不一致 = バックテストとライブ実装の乖離バグ（許容差ゼロ）。

使い方
------
CLI（ペーパートレード開始前の必須ゲート）:

    uv run python scripts/check_parity.py \\
        --start 2024-10-01 --end 2024-12-31

ライブ週次チェック（Phase 4 日次バッチに組み込み予定）:

    from src.validation.parity_checker import ParityChecker
    checker = ParityChecker(scenarios)
    report = checker.check_recent(data, lookback_days=30)
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
class ParityMismatch:
    check_date: date
    symbol: str
    scenario_id: str
    backtest_action: str  # action from pre-computed (full-data) signals
    live_action: str  # action from day-by-day re-run (truncated data)


@dataclass
class ParityReport:
    start: date
    end: date
    total_checked: int
    mismatches: list[ParityMismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.mismatches) == 0

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"[{status}] Parity check {self.start} → {self.end}",
            f"  Checked: {self.total_checked} (symbol, date, scenario) tuples",
            f"  Mismatches: {len(self.mismatches)}",
        ]
        if self.mismatches:
            lines.append("  First mismatches:")
            for m in self.mismatches[:5]:
                lines.append(
                    f"    {m.check_date} {m.symbol} [{m.scenario_id}]: "
                    f"backtest={m.backtest_action!r} live={m.live_action!r}"
                )
            if len(self.mismatches) > 5:
                lines.append(f"    ... and {len(self.mismatches) - 5} more")
        return "\n".join(lines)


# ── Checker ───────────────────────────────────────────────────────────────────


class ParityChecker:
    """Verify that backtest and live signal generation are identical.

    The "backtest" signals are produced by calling ``generate_signals`` on
    the full historical dataset (the same way the backtest engine does it).
    The "live" signals are produced by calling ``generate_signals`` on data
    truncated to each target date — simulating what daily_signals.py would
    produce if run on that day with only data available at that point.

    Complete agreement is required (zero tolerance).
    """

    def __init__(
        self,
        scenarios: list[ScenarioBase],
        raise_on_mismatch: bool = True,
    ) -> None:
        self.scenarios = scenarios
        self.raise_on_mismatch = raise_on_mismatch

    def check(
        self,
        data: pl.DataFrame,
        start: date,
        end: date,
    ) -> ParityReport:
        """Check signal parity for [start, end] using historical data.

        Args:
            data:  Full OHLCV + indicator dataset (all dates, all symbols).
                   May include dates before start for indicator warm-up.
            start: First date to verify (inclusive).
            end:   Last date to verify (inclusive).
        """
        check_dates = sorted(
            d
            for d in data.filter((pl.col("date") >= start) & (pl.col("date") <= end))["date"]
            .unique()
            .to_list()
        )

        report = ParityReport(start=start, end=end, total_checked=0)

        for scenario in self.scenarios:
            # Backtest signals: generate_signals on full dataset per symbol
            bt_signals = self._precompute(scenario, data)

            # Live signals: generate_signals on truncated data per check date
            for check_date in check_dates:
                trunc = data.filter(pl.col("date") <= check_date)
                live_signals = self._precompute(scenario, trunc)

                # Compare on the check date only
                for sym in data["symbol"].unique().to_list():
                    bt_action = bt_signals.get(sym, {}).get(check_date, "")
                    live_action = live_signals.get(sym, {}).get(check_date, "")
                    report.total_checked += 1

                    if bt_action != live_action:
                        mismatch = ParityMismatch(
                            check_date=check_date,
                            symbol=sym,
                            scenario_id=scenario.scenario_id,
                            backtest_action=bt_action,
                            live_action=live_action,
                        )
                        report.mismatches.append(mismatch)
                        logger.error(
                            f"parity MISMATCH: {scenario.scenario_id}/{sym} @ {check_date} "
                            f"bt={bt_action!r} live={live_action!r}"
                        )
                        if self.raise_on_mismatch:
                            raise ParityError(report)

        logger.info(report.summary())
        return report

    def check_recent(
        self,
        data: pl.DataFrame,
        lookback_days: int = 30,
    ) -> ParityReport:
        """Convenience: check the most recent N calendar days in data."""
        all_dates = sorted(data["date"].unique().to_list())
        if not all_dates:
            return ParityReport(start=date.today(), end=date.today(), total_checked=0)
        end = all_dates[-1]
        from datetime import timedelta

        start = max(all_dates[0], end - timedelta(days=lookback_days))
        return self.check(data, start, end)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _precompute(
        self,
        scenario: ScenarioBase,
        data: pl.DataFrame,
    ) -> dict[str, dict[date, str]]:
        """Return {symbol: {date: action}} from generate_signals."""
        result: dict[str, dict[date, str]] = {}
        for sym in data["symbol"].unique().to_list():
            sym_data = data.filter(pl.col("symbol") == sym).sort("date")
            if sym_data.is_empty():
                continue
            try:
                sig_df = scenario.generate_signals(sym_data)
                result[sym] = {row["date"]: row["action"] for row in sig_df.iter_rows(named=True)}
            except Exception as exc:
                logger.warning(f"parity: {scenario.scenario_id}/{sym} error: {exc}")
        return result


class ParityError(Exception):
    """Raised when backtest/live signals diverge (raise_on_mismatch=True)."""

    def __init__(self, report: ParityReport) -> None:
        self.report = report
        super().__init__(
            f"Parity check FAILED: {len(report.mismatches)} mismatches detected. "
            "Backtest and live implementations have diverged. "
            "Do NOT start paper trading until this is resolved."
        )
