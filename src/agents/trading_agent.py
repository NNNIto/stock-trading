"""TradingAgent: thin orchestrator wrapping scripts/daily_signals.run().

dry_run=True (default): fetches and logs signals without any DB writes or real orders.
dry_run=False: writes positions to PortfolioManager (paper mode) and sends Slack notifications.
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any

from src.notification.slack import SlackNotifier
from src.utils.logger import get_logger

logger = get_logger()

_RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
_SCRIPTS_DIR = str(Path(__file__).parent.parent.parent / "scripts")


class TradingAgent:
    """Orchestrates the daily trading pipeline.

    Delegates signal generation to scripts/daily_signals.run() to avoid
    duplicating orchestration logic.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run
        self.notifier = SlackNotifier()

    def run_daily(self, signal_date: date | None = None) -> None:
        """Run the full daily pipeline: data → signals → risk → notifications.

        Adds scripts/ to sys.path once, then delegates to daily_signals.run().
        """
        if _SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, _SCRIPTS_DIR)
        import daily_signals  # type: ignore[import]

        run_date = signal_date or date.today()
        logger.info(f"TradingAgent.run_daily: {run_date} dry_run={self.dry_run}")
        daily_signals.run(signal_date=run_date, dry_run=self.dry_run)

    def execute_order(self, signal: dict[str, Any], quantity: int) -> None:
        """Record or execute a single order.

        dry_run=True: log to stdout + append to results/signals_YYYYMMDD.csv.
        dry_run=False: write to PortfolioManager (paper mode) + Slack notification.
        """
        symbol = signal.get("symbol", "?")
        scenario = signal.get("scenario_id", "?")
        price = float(signal.get("price") or signal.get("expected_entry_price") or 0.0)
        today = date.today()

        if self.dry_run:
            price_str = f"¥{price:,.2f}" if price else "(price unknown)"
            logger.info(f"[DRY RUN] BUY {symbol} [{scenario}] {price_str} × {quantity}株")
            self._record_csv(signal, quantity, today)
        else:
            from src.portfolio.manager import PortfolioManager

            with PortfolioManager() as pm:
                pm.open_position(
                    symbol=symbol,
                    scenario_id=scenario,
                    market=signal.get("market", "US"),
                    entry_date=today,
                    entry_price=price,
                    quantity=quantity,
                    mode="paper",
                )
            self.notifier.notify_order_executed(
                {"symbol": symbol, "quantity": quantity, "price": price, "mode": "paper"}
            )

    def _record_csv(self, signal: dict[str, Any], quantity: int, today: date) -> None:
        _RESULTS_DIR.mkdir(exist_ok=True)
        csv_path = _RESULTS_DIR / f"signals_{today.strftime('%Y%m%d')}.csv"
        is_new = not csv_path.exists()
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["date", "symbol", "scenario_id", "market", "quantity", "price"],
            )
            if is_new:
                writer.writeheader()
            writer.writerow(
                {
                    "date": today.isoformat(),
                    "symbol": signal.get("symbol", ""),
                    "scenario_id": signal.get("scenario_id", ""),
                    "market": signal.get("market", ""),
                    "quantity": quantity,
                    "price": signal.get("price") or signal.get("expected_entry_price") or "",
                }
            )
