"""Daily signal generation batch (cron: 06:05 JST daily).

Flow (architecture.md 8.1):
  1. Load latest OHLCV from DB + compute indicators
  2. Generate signals for all enabled scenarios
  3. Exit check for open positions
  4. Conflict resolution (scenarios.md 6)
  5. Macro filter
  6. Write to signals table (idempotent: skip if today's signals exist)
  7. Slack notification
  Weekly: run parity check on recent 30 days

Usage:
    uv run python scripts/daily_signals.py [--date YYYY-MM-DD] [--dry-run]

Options:
    --date      Override signal date (default: today's date)
    --dry-run   Generate signals but do not write to DB or send Slack
    --parity    Also run parity check (normally weekly on Mondays)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl

from src.backtest.engine import BacktestEngine
from src.backtest.execution import ExecutionConfig
from src.data.indicators import add_indicators_batch
from src.data.repository import Repository
from src.notification.slack import (
    notify_daily_summary,
    notify_error,
    notify_exit_signal,
    notify_new_signals,
)
from src.portfolio.manager import PortfolioManager
from src.portfolio.sizer import build_sizer
from src.scenarios.s2_breakout import S2Breakout
from src.scenarios.s3_pullback import S3Pullback
from src.scenarios.s4_pead import S4PEAD
from src.scenarios.s6_reversion import S6Reversion
from src.utils.config import get_settings
from src.utils.logger import get_logger, setup_logger
from src.validation.parity_checker import ParityChecker

setup_logger()
logger = get_logger()

_LOOKBACK_DAYS = 400  # days of history for indicator warm-up


def _load_recent_data(
    settings: object,
    signal_date: date,
) -> pl.DataFrame:
    """Load OHLCV from DB for the last _LOOKBACK_DAYS days + compute indicators."""
    start = signal_date - timedelta(days=_LOOKBACK_DAYS)
    with Repository() as repo:
        df = repo.query_ohlcv(start=start.isoformat(), end=signal_date.isoformat())
    if df.is_empty():
        logger.warning("daily_signals: no OHLCV data found in DB")
        return df
    return add_indicators_batch(df)


def _signals_already_exist(repo: Repository, signal_date: date) -> bool:
    """Check if signals for today already exist (idempotency guard)."""
    try:
        result = repo._conn.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_date = ?",
            [signal_date],
        ).fetchone()
        return bool(result and result[0] > 0)
    except Exception:
        return False


def _store_signals(
    repo: Repository,
    signals: list[dict],
    signal_date: date,
    scenario_version: str = "",
) -> None:
    """Write approved signals to the signals table."""
    import uuid
    from datetime import datetime

    for s in signals:
        try:
            repo._conn.execute(
                """
                INSERT INTO signals
                  (signal_id, generated_at, scenario_id, scenario_version,
                   symbol, action, signal_date, expected_entry_price, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    str(uuid.uuid4()),
                    datetime.now().isoformat(),
                    s.get("scenario_id", ""),
                    scenario_version,
                    s.get("symbol", ""),
                    "BUY",
                    signal_date,
                    s.get("expected_entry_price"),
                    "{}",
                ],
            )
        except Exception as exc:
            logger.warning(f"daily_signals: failed to store signal {s}: {exc}")


def _ensure_signals_table(repo: Repository) -> None:
    repo._conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            signal_id        VARCHAR PRIMARY KEY,
            generated_at     TIMESTAMP NOT NULL,
            scenario_id      VARCHAR NOT NULL,
            scenario_version VARCHAR NOT NULL,
            symbol           VARCHAR NOT NULL,
            action           VARCHAR NOT NULL,
            signal_date      DATE NOT NULL,
            expected_entry_price DOUBLE,
            metadata         JSON
        )
    """)


def run(
    signal_date: date,
    dry_run: bool = False,
    run_parity: bool = False,
) -> None:
    settings = get_settings()
    scenarios = [S2Breakout(), S3Pullback(), S4PEAD(), S6Reversion()]

    data = _load_recent_data(settings, signal_date)
    if data.is_empty():
        notify_error(
            f"daily_signals ({signal_date}): DB empty — data_update.py を先に実行してください"
        )
        return

    logger.info(f"daily_signals: {signal_date} — {data['symbol'].n_unique()} symbols loaded")

    # ── Weekly parity check (Mondays) ────────────────────────────────────────
    if run_parity or signal_date.weekday() == 0:
        logger.info("daily_signals: running parity check …")
        checker = ParityChecker(scenarios, raise_on_mismatch=False)
        parity = checker.check_recent(data, lookback_days=30)
        if not parity.passed:
            msg = f"パリティ検証FAIL: {len(parity.mismatches)}件の不一致\n{parity.summary()}"
            logger.error(msg)
            notify_error(msg)
        else:
            logger.info("parity check: PASS")

    # ── Generate signals via BacktestEngine (single day) ─────────────────────
    exec_cfg = ExecutionConfig(
        slippage_pct=settings.execution.slippage_pct,
        commission_pct=settings.execution.commission_pct,
        fx_cost_pct=settings.execution.fx_cost_pct,
    )
    engine = BacktestEngine(
        scenarios=scenarios,
        sizer=build_sizer(settings),
        exec_config=exec_cfg,
        initial_capital=float(settings.project.capital_jpy),
        max_positions=settings.risk.max_positions,
        random_seed=settings.backtest.random_seed,
    )

    # Run engine for signal_date only (history used for indicator context)
    result = engine.run(data, signal_date, signal_date)
    buy_trades = result.trades.filter(
        (pl.col("exit_reason") == "end_of_backtest") | (pl.col("entry_date") == signal_date)
    )

    # Build notification payload
    new_signals_payload: list[dict] = []
    for row in buy_trades.filter(pl.col("entry_date") == signal_date).iter_rows(named=True):
        new_signals_payload.append(
            {
                "symbol": row["symbol"],
                "scenario_id": row["scenario_id"],
                "expected_entry_price": row.get("entry_price"),
            }
        )

    # ── Exit signals from open positions ─────────────────────────────────────
    exit_signals: list[dict] = []
    with PortfolioManager() as pm:
        open_pos = pm.get_open_positions(mode="paper")
        if open_pos.height > 0:
            # Update to today's close prices
            sym_price = {}
            if "close" in data.columns:
                today = data.filter(pl.col("date") == signal_date)
                for r in today.iter_rows(named=True):
                    sym_price[r["symbol"]] = r["close"]
            pm.update_prices_bulk(sym_price)
            # Check exit signals
            for pos_row in open_pos.iter_rows(named=True):
                sym = pos_row["symbol"]
                price = sym_price.get(sym, pos_row.get("entry_price", 0))
                entry_p = pos_row.get("entry_price", 0)
                pnl = (price - entry_p) * pos_row.get("quantity", 0)
                exit_signals.append({"symbol": sym, "current_price": price, "pnl": pnl})

    # ── Write to DB (idempotent) ──────────────────────────────────────────────
    if not dry_run and new_signals_payload:
        with Repository() as repo:
            _ensure_signals_table(repo)
            if not _signals_already_exist(repo, signal_date):
                _store_signals(repo, new_signals_payload, signal_date)
                logger.info(f"daily_signals: stored {len(new_signals_payload)} signals")
            else:
                logger.info(f"daily_signals: signals for {signal_date} already exist — skipping")
    elif dry_run:
        logger.info(
            f"daily_signals [DRY RUN]: {len(new_signals_payload)} signals, "
            f"{len(exit_signals)} exits"
        )

    # ── Slack notifications ───────────────────────────────────────────────────
    if not dry_run:
        notify_new_signals(new_signals_payload, signal_date)
        for ex in exit_signals:
            notify_exit_signal(
                symbol=ex["symbol"],
                exit_reason="exit_check",
                entry_price=0.0,
                current_price=ex.get("current_price", 0.0),
                pnl=ex.get("pnl"),
            )
        notify_daily_summary(
            signal_date=signal_date,
            new_signals=len(new_signals_payload),
            exit_signals=len(exit_signals),
            open_positions=len(exit_signals),
            portfolio_value=float(settings.project.capital_jpy),
        )

    logger.info(
        f"daily_signals: done — {len(new_signals_payload)} new signals, "
        f"{len(exit_signals)} exits"
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate daily trading signals")
    p.add_argument("--date", help="Signal date YYYY-MM-DD (default: today)")
    p.add_argument("--dry-run", action="store_true", help="Skip DB writes and Slack")
    p.add_argument("--parity", action="store_true", help="Force parity check")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    signal_date = date.fromisoformat(args.date) if args.date else date.today()
    run(signal_date=signal_date, dry_run=args.dry_run, run_parity=args.parity)
