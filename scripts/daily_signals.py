"""Daily signal generation batch (cron: 06:05 JST daily).

Flow (architecture.md 8.1):
  1. Load latest OHLCV + compute indicators
  2. Call scenario.generate_signals() directly for each scenario × symbol
  3. Filter today's BUY signals; apply conflict resolution (scenarios.md 6)
  4. Check exit signals for open positions via scenario.get_exit_signal()
  5. Apply macro filter
  6. Write to signals table (idempotent: skip if today's signals exist)
  7. Slack notification
  Weekly (Mondays): run parity check on recent 30 days

Usage:
    uv run python scripts/daily_signals.py [--date YYYY-MM-DD] [--dry-run] [--parity]

Options:
    --date      Override signal date (default: today)
    --dry-run   Generate signals but do not write to DB or Slack
    --parity    Force parity check regardless of weekday
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl

from src.data.indicators import add_indicators_batch
from src.data.repository import Repository
from src.data.universe import get_liquid_symbols
from src.notification.slack import (
    notify_daily_summary,
    notify_error,
    notify_exit_signal,
    notify_new_signals,
)
from src.portfolio.manager import PortfolioManager
from src.scenarios.base import ExitReason, Position, ScenarioBase
from src.scenarios.s2_breakout import S2Breakout
from src.scenarios.s3_pullback import S3Pullback
from src.scenarios.s4_pead import S4PEAD
from src.scenarios.s6_reversion import S6Reversion
from src.utils.config import get_settings
from src.utils.logger import get_logger, setup_logger
from src.validation.parity_checker import ParityChecker

setup_logger()
logger = get_logger()

_LOOKBACK_DAYS = 400  # days of history required for indicator warm-up
_SCENARIO_PRIORITY: dict[str, int] = {"S6": 0, "S3": 1, "S2": 2, "S4": 3}


# ── Data loading ──────────────────────────────────────────────────────────────


def _get_liquid_symbols(signal_date: date) -> list[str] | None:
    """Return liquidity-filtered symbol list; None if filter disabled."""
    from src.utils.config import get_settings

    uf = get_settings().universe_filter
    if not uf.enabled:
        return None
    result: list[str] = []
    with Repository() as repo:
        for mkt, n_top in [("JP", uf.jp_top_n), ("US", uf.us_top_n)]:
            selected = get_liquid_symbols(repo, mkt, n_top, signal_date, uf.lookback_years)
            result.extend(selected)
    logger.info(f"universe filter: {len(result)} liquid symbols selected")
    return result or None


def _load_recent_data(signal_date: date) -> pl.DataFrame:
    """Load OHLCV from DB + compute indicators; return empty DF if no data."""
    start = signal_date - timedelta(days=_LOOKBACK_DAYS)
    symbols = _get_liquid_symbols(signal_date)
    with Repository() as repo:
        df = repo.query_ohlcv(symbols=symbols, start=start.isoformat(), end=signal_date.isoformat())
    if df.is_empty():
        logger.warning("daily_signals: no OHLCV data found in DB")
        return df
    return add_indicators_batch(df)


# ── Signal generation ─────────────────────────────────────────────────────────


def _generate_buy_signals(
    scenarios: list[ScenarioBase],
    data: pl.DataFrame,
    signal_date: date,
) -> list[dict[str, Any]]:
    """Call generate_signals() on each scenario×symbol; return today's BUY rows."""
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        priority = _SCENARIO_PRIORITY.get(scenario.scenario_id, 0)
        for sym in data["symbol"].unique().to_list():
            sym_data = data.filter(pl.col("symbol") == sym).sort("date")
            if sym_data.is_empty():
                continue
            market = str(sym_data["market"][0]) if "market" in sym_data.columns else "JP"
            if not scenario.is_enabled_for_market(market):
                continue
            try:
                sig_df = scenario.generate_signals(sym_data)
                today_buys = sig_df.filter(
                    (pl.col("date") == signal_date) & (pl.col("action") == "BUY")
                )
                if today_buys.height > 0:
                    close_price: float | None = None
                    last_row = sym_data.filter(pl.col("date") == signal_date)
                    if last_row.height > 0 and "close" in last_row.columns:
                        close_price = float(last_row["close"][0])
                    results.append(
                        {
                            "symbol": sym,
                            "scenario_id": scenario.scenario_id,
                            "market": market,
                            "priority": priority,
                            "close": close_price,
                        }
                    )
            except Exception as exc:
                logger.warning(
                    f"daily_signals: generate_signals error {scenario.scenario_id}/{sym}: {exc}"
                )
    return results


# ── Exit check ────────────────────────────────────────────────────────────────


def _check_exit_signals(
    scenarios: list[ScenarioBase],
    open_positions: pl.DataFrame,
    data: pl.DataFrame,
    signal_date: date,
) -> list[dict[str, Any]]:
    """Call get_exit_signal() for each open position using today's data."""
    if open_positions.is_empty():
        return []

    # Build {symbol: row_dict} for today
    today_rows: dict[str, dict[str, Any]] = {
        r["symbol"]: r for r in data.filter(pl.col("date") == signal_date).iter_rows(named=True)
    }
    scenario_map = {s.scenario_id: s for s in scenarios}
    exits: list[dict[str, Any]] = []

    for pos_row in open_positions.iter_rows(named=True):
        sym = pos_row["symbol"]
        today = today_rows.get(sym)
        if today is None:
            continue
        scenario = scenario_map.get(pos_row.get("scenario_id", ""))
        if scenario is None:
            continue

        entry_date: date = pos_row["entry_date"]
        entry_price: float = float(pos_row["entry_price"])
        current_price: float = float(today.get("close", entry_price))
        holding = (signal_date - entry_date).days

        position = Position(
            symbol=sym,
            scenario_id=pos_row.get("scenario_id", ""),
            entry_date=entry_date,
            entry_price=entry_price,
            quantity=int(pos_row.get("quantity", 0)),
            market=pos_row.get("market", "JP"),
            peak_price=float(pos_row.get("current_price") or entry_price),
            holding_days=holding,
        )

        reason = scenario.get_exit_signal(position, today)
        if reason != ExitReason.NO_EXIT:
            pnl = (current_price - entry_price) * position.quantity
            exits.append(
                {
                    "symbol": sym,
                    "exit_reason": reason,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "pnl": pnl,
                }
            )

    return exits


# ── Conflict resolution ───────────────────────────────────────────────────────


def _resolve_conflicts(
    signals: list[dict[str, Any]],
    open_positions: pl.DataFrame,
    max_positions: int,
) -> list[dict[str, Any]]:
    """Apply scenarios.md section 6 rules (de-dup, position limit)."""
    open_syms = set(open_positions["symbol"].to_list()) if not open_positions.is_empty() else set()
    n_open = len(open_syms)

    # De-duplicate: per symbol keep highest priority
    best: dict[str, dict[str, Any]] = {}
    for sig in signals:
        sym = sig["symbol"]
        if sym not in best or sig["priority"] > best[sym]["priority"]:
            best[sym] = sig

    approved: list[dict[str, Any]] = []
    for sig in sorted(best.values(), key=lambda s: -s["priority"]):
        if sig["symbol"] in open_syms:
            continue
        if n_open >= max_positions:
            break
        approved.append(sig)
        n_open += 1

    return approved


# ── Main ──────────────────────────────────────────────────────────────────────


def run(
    signal_date: date,
    dry_run: bool = False,
    run_parity: bool = False,
) -> None:
    settings = get_settings()
    scenarios: list[ScenarioBase] = [S2Breakout(), S3Pullback(), S4PEAD(), S6Reversion()]

    # Step 1: Load data
    data = _load_recent_data(signal_date)
    if data.is_empty():
        notify_error(f"daily_signals ({signal_date}): DB空 — data_update.py を先に実行")
        return

    logger.info(f"daily_signals: {signal_date} — {data['symbol'].n_unique()} symbols")

    # Step 2: Weekly parity check (Mondays or forced)
    if run_parity or signal_date.weekday() == 0:
        logger.info("daily_signals: parity check …")
        checker = ParityChecker(scenarios, raise_on_mismatch=False)
        parity = checker.check_recent(data, lookback_days=30)
        if not parity.passed:
            msg = f"パリティ検証FAIL: {len(parity.mismatches)}件\n{parity.summary()}"
            logger.error(msg)
            notify_error(msg)
        else:
            logger.info("parity: PASS")

    # Step 3: Load open positions + check exits
    with PortfolioManager() as pm:
        open_pos = pm.get_open_positions(mode="paper")
        exit_signals = _check_exit_signals(scenarios, open_pos, data, signal_date)

        # Update MTM prices
        today_prices = {
            r["symbol"]: float(r["close"])
            for r in data.filter(pl.col("date") == signal_date).iter_rows(named=True)
            if "close" in r
        }
        pm.update_prices_bulk(today_prices)

    # Step 4: Generate BUY signals directly from each scenario
    raw_signals = _generate_buy_signals(scenarios, data, signal_date)
    logger.info(f"daily_signals: {len(raw_signals)} raw signals before resolution")

    # Step 5: Conflict resolution
    approved = _resolve_conflicts(raw_signals, open_pos, settings.risk.max_positions)
    logger.info(f"daily_signals: {len(approved)} approved signals")

    # Step 6: Write to DB (idempotent)
    if not dry_run:
        with Repository() as repo:
            repo.ensure_signals_table()
            if repo.signals_exist_for_date(signal_date):
                logger.info(f"daily_signals: signals for {signal_date} already exist — skip")
            else:
                for sig in approved:
                    repo.upsert_signal(
                        signal_id=str(uuid.uuid4()),
                        scenario_id=sig["scenario_id"],
                        scenario_version="",
                        symbol=sig["symbol"],
                        action="BUY",
                        signal_date=signal_date,
                        expected_entry_price=sig.get("close"),
                    )
                logger.info(f"daily_signals: stored {len(approved)} signals")
    else:
        logger.info(f"daily_signals [DRY RUN]: {len(approved)} signals, {len(exit_signals)} exits")

    # Step 7: Slack notifications
    if not dry_run:
        slack_signals = [{"symbol": s["symbol"], "scenario_id": s["scenario_id"]} for s in approved]
        notify_new_signals(slack_signals, signal_date)
        for ex in exit_signals:
            notify_exit_signal(
                symbol=ex["symbol"],
                exit_reason=ex["exit_reason"],
                entry_price=ex["entry_price"],
                current_price=ex["current_price"],
                pnl=ex["pnl"],
            )
        notify_daily_summary(
            signal_date=signal_date,
            new_signals=len(approved),
            exit_signals=len(exit_signals),
            open_positions=open_pos.height,
            portfolio_value=float(settings.project.capital_jpy),
        )

    logger.info(f"daily_signals: done — {len(approved)} new, {len(exit_signals)} exits")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate daily trading signals")
    p.add_argument("--date", help="Signal date YYYY-MM-DD (default: today)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--parity", action="store_true", help="Force parity check")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    signal_date = date.fromisoformat(args.date) if args.date else date.today()
    run(signal_date=signal_date, dry_run=args.dry_run, run_parity=args.parity)
