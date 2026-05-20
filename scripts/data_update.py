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
) -> None:
    """Fetch and store OHLCV + FX data.

    If start is None, performs incremental update from last stored date.
    If start is provided, performs historical load from that date.
    """
    settings = get_settings()
    source = build_default_source(settings)
    end = end or date.today()

    with Repository() as repo:
        # Determine symbols and market
        markets = [market] if market else ["JP", "US"]

        for mkt in markets:
            universe_df = load_universe(mkt)
            syms = symbols or universe_df["symbol"].to_list()
            logger.info(f"Processing {mkt}: {len(syms)} symbols")

            # Determine start date
            if start is None:
                # Incremental: use last stored date
                sample_sym = syms[0] if syms else None
                if sample_sym:
                    _, last_date = repo.get_date_range(sample_sym)
                    fetch_start = (
                        date.fromisoformat(last_date) + timedelta(days=1)
                        if last_date
                        else date.fromisoformat(settings.data.start_date)
                    )
                else:
                    fetch_start = date.fromisoformat(settings.data.start_date)
            else:
                fetch_start = start

            if fetch_start > end:
                logger.info(f"{mkt}: already up to date")
                continue

            logger.info(f"{mkt}: fetching {fetch_start} to {end} for {len(syms)} symbols")

            # Fetch in batches of 50 to avoid yfinance limits
            batch_size = 50
            for i in range(0, len(syms), batch_size):
                batch = syms[i:i + batch_size]
                logger.info(f"{mkt}: batch {i // batch_size + 1}/{(len(syms) - 1) // batch_size + 1}")
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

        # Fetch USD/JPY
        fx_start = start or date.fromisoformat(settings.data.start_date)
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
    args = parser.parse_args()

    run_update(
        market=args.market,
        start=date.fromisoformat(args.start) if args.start else None,
        end=date.fromisoformat(args.end) if args.end else None,
    )
