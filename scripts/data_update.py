"""Data update batch: fetch → quality check → DuckDB upsert.

Supports both initial historical load (2018–) and daily incremental update.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.fetcher import build_default_source
from src.data.quality import clean_ohlcv, run_batch_quality_check
from src.data.repository import Repository
from src.data.universe import load_universe
from src.utils.config import get_settings
from src.utils.logger import get_logger, setup_logger

setup_logger()
logger = get_logger()


def run_update(
    market: str | None = None,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
    with_earnings: bool = False,
) -> None:
    """Fetch and store OHLCV + FX (+ optionally earnings) data.

    If start is None, performs incremental update from last stored date.
    If start is provided, performs historical load from that date.
    """
    settings = get_settings()
    source = build_default_source(settings)
    end = end or date.today()

    with Repository() as repo:
        markets = [market] if market else ["JP", "US"]

        # ── Universe sync ─────────────────────────────────────────────────────
        for mkt in markets:
            univ = load_universe(mkt)
            repo.upsert_universe(univ)
            logger.info(f"universe: synced {univ.height} active {mkt} symbols to DB")

        for mkt in markets:
            universe_df = load_universe(mkt)
            syms = symbols or universe_df["symbol"].to_list()
            logger.info(f"Processing {mkt}: {len(syms)} symbols")

            # ── OHLCV ────────────────────────────────────────────────────────
            if start is None:
                min_last = repo.get_min_last_date(syms) if syms else None
                fetch_start = (
                    date.fromisoformat(min_last) + timedelta(days=1)
                    if min_last
                    else date.fromisoformat(settings.data.start_date)
                )
            else:
                fetch_start = start

            if fetch_start > end:
                logger.info(f"{mkt}: already up to date")
            else:
                logger.info(f"{mkt}: fetching {fetch_start} to {end} for {len(syms)} symbols")
                batch_size = 50
                for i in range(0, len(syms), batch_size):
                    batch = syms[i : i + batch_size]
                    logger.info(
                        f"{mkt}: batch {i // batch_size + 1}/{(len(syms) - 1) // batch_size + 1}"
                    )
                    try:
                        raw = source.fetch_ohlcv(batch, fetch_start, end, mkt)
                        if raw.is_empty():
                            logger.warning(f"{mkt}: empty data for batch {batch[:3]}...")
                            continue
                        cleaned = clean_ohlcv(raw)
                        reports = run_batch_quality_check(cleaned)
                        failed = [r for r in reports if not r.passed]
                        if failed:
                            logger.warning(f"{mkt}: {len(failed)} symbols failed quality check")
                        repo.upsert_ohlcv(cleaned)
                    except Exception as e:
                        logger.error(f"{mkt}: batch error: {e}")

            # ── Earnings (optional) ──────────────────────────────────────────
            if with_earnings:
                logger.info(f"{mkt}: fetching earnings for {len(syms)} symbols")
                ok = err = 0
                for sym in syms:
                    try:
                        df = source.fetch_earnings(sym)
                        if not df.is_empty():
                            repo.upsert_earnings(df)
                            ok += 1
                    except Exception as e:
                        logger.warning(f"{mkt}: earnings error for {sym}: {e}")
                        err += 1
                logger.info(f"{mkt}: earnings done — ok={ok} err={err}")

        # ── USD/JPY FX ────────────────────────────────────────────────────────
        if start is None:
            fx_last = repo.get_fx_last_date("USDJPY")
            fx_start = (
                date.fromisoformat(fx_last) + timedelta(days=1)
                if fx_last
                else date.fromisoformat(settings.data.start_date)
            )
        else:
            fx_start = start

        if fx_start > end:
            logger.info("FX: already up to date")
        else:
            logger.info(f"Fetching USD/JPY FX {fx_start} to {end}")
            try:
                fx_df = source.fetch_fx("USDJPY=X", fx_start, end)
                repo.upsert_fx("USDJPY", fx_df)
                logger.info(f"FX: stored {fx_df.height} rows")
            except Exception as e:
                logger.error(f"FX fetch failed: {e}")

    logger.info("Data update complete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update market data")
    parser.add_argument("--market", choices=["JP", "US"], help="Market to update (default: both)")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: incremental)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--with-earnings",
        action="store_true",
        help="Also fetch earnings data (slow: 1 request/symbol)",
    )
    args = parser.parse_args()

    run_update(
        market=args.market,
        start=date.fromisoformat(args.start) if args.start else None,
        end=date.fromisoformat(args.end) if args.end else None,
        with_earnings=args.with_earnings,
    )
