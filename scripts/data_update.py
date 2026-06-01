"""Data update batch: fetch → quality check → DuckDB upsert.

Supports both initial historical load (2018–) and daily incremental update.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

# Allow running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.fetcher import JQuantsSource, build_default_source
from src.data.quality import clean_ohlcv, run_batch_quality_check
from src.data.repository import Repository
from src.data.universe import load_universe
from src.utils.config import get_settings
from src.utils.logger import get_logger, setup_logger

setup_logger()
logger = get_logger()

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 15  # seconds; doubles each attempt


@dataclass
class _Summary:
    batches_ok: int = 0
    batches_err: int = 0
    rows_stored: int = 0
    nan_warnings: list[str] = field(default_factory=list)

    def log(self) -> None:
        logger.info("========== Data Update Summary ==========")
        logger.info(f"  Batches succeeded : {self.batches_ok}")
        logger.info(f"  Batches failed    : {self.batches_err}")
        logger.info(f"  Rows stored       : {self.rows_stored}")
        if self.nan_warnings:
            logger.warning(f"  NaN warnings      : {len(self.nan_warnings)}")
            for w in self.nan_warnings[:10]:
                logger.warning(f"    {w}")
            if len(self.nan_warnings) > 10:
                logger.warning(f"    … and {len(self.nan_warnings) - 10} more")
        logger.info("=========================================")


def _fetch_with_retry(fn: Any, label: str) -> Any:
    """Call fn(); retry up to _MAX_RETRIES times with exponential backoff."""
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2**attempt)
            logger.warning(f"{label}: attempt {attempt + 1} failed ({e}). retry in {delay}s…")
            time.sleep(delay)
    return None  # unreachable


def _check_nan(df: pl.DataFrame, label: str, summary: _Summary) -> None:
    """Detect null and float NaN values in OHLCV columns and emit warnings.

    Polars distinguishes null (missing) from float NaN (0/0 arithmetic).
    Both are checked separately since clean_ohlcv() only drops nulls.
    """
    ohlcv_cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    for col in ohlcv_cols:
        null_count = df[col].null_count()
        if null_count > 0:
            msg = f"{label}: {null_count} null values in '{col}'"
            logger.warning(msg)
            summary.nan_warnings.append(msg)
        if df[col].dtype in (pl.Float32, pl.Float64):
            nan_count = int(df[col].is_nan().sum() or 0)
            if nan_count > 0:
                msg = f"{label}: {nan_count} float NaN values in '{col}'"
                logger.warning(msg)
                summary.nan_warnings.append(msg)


def run_update(
    market: str | None = None,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
    with_earnings: bool = False,
    dry_run: bool = False,
) -> None:
    """Fetch and store OHLCV + FX (+ optionally earnings) data.

    If start is None, performs incremental update from last stored date.
    If start is provided, performs historical load from that date.
    dry_run=True fetches and validates data but skips all DB writes.
    """
    if dry_run:
        logger.info("[DRY RUN] No data will be written to the database.")

    settings = get_settings()
    source = build_default_source(settings)
    end = end or date.today()
    summary = _Summary()

    with Repository() as repo:
        markets = [market] if market else ["JP", "US"]

        # ── Universe sync ─────────────────────────────────────────────────────
        for mkt in markets:
            univ = load_universe(mkt)
            if not dry_run:
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
                    batch_label = (
                        f"{mkt} batch {i // batch_size + 1}/{(len(syms) - 1) // batch_size + 1}"
                    )
                    logger.info(batch_label)
                    try:
                        raw = _fetch_with_retry(
                            lambda b=batch, fs=fetch_start, m=mkt: source.fetch_ohlcv(
                                b, fs, end, m
                            ),
                            batch_label,
                        )
                        if raw is None or raw.is_empty():
                            logger.warning(f"{mkt}: empty data for batch {batch[:3]}...")
                            summary.batches_err += 1
                            continue
                        cleaned = clean_ohlcv(raw)
                        _check_nan(cleaned, batch_label, summary)
                        reports = run_batch_quality_check(cleaned)
                        failed = [r for r in reports if not r.passed]
                        if failed:
                            logger.warning(f"{mkt}: {len(failed)} symbols failed quality check")
                        if not dry_run:
                            repo.upsert_ohlcv(cleaned)
                            summary.rows_stored += cleaned.height
                        else:
                            logger.info(f"[DRY RUN] would store {cleaned.height} rows")
                        summary.batches_ok += 1
                    except Exception as e:
                        logger.error(f"{mkt}: batch failed after {_MAX_RETRIES} retries: {e}")
                        summary.batches_err += 1

            # ── Earnings (optional) ──────────────────────────────────────────
            if with_earnings:
                logger.info(f"{mkt}: fetching earnings for {len(syms)} symbols")
                ok = err = 0
                for sym in syms:
                    try:
                        df = source.fetch_earnings(sym)
                        if not df.is_empty():
                            if not dry_run:
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
                fx_df = _fetch_with_retry(
                    lambda fs=fx_start: source.fetch_fx("USDJPY=X", fs, end),
                    "FX USDJPY",
                )
                if fx_df is not None and not fx_df.is_empty():
                    _check_nan(fx_df, "FX USDJPY", summary)
                    if not dry_run:
                        repo.upsert_fx("USDJPY", fx_df)
                        summary.rows_stored += fx_df.height
                        logger.info(f"FX: stored {fx_df.height} rows")
                    else:
                        logger.info(f"[DRY RUN] would store {fx_df.height} FX rows")
            except Exception as e:
                logger.error(f"FX fetch failed after {_MAX_RETRIES} retries: {e}")
                summary.batches_err += 1

    logger.info("Data update complete")
    summary.log()


def run_bulk_earnings(
    start: date,
    end: date,
    cache_dir: str = "data/jquants_cache",
    universe_filter: list[str] | None = None,
) -> None:
    """Fetch all JP earnings in bulk via J-Quants get_fin_summary_range.

    Uses cache_dir to avoid re-fetching already-downloaded dates.
    For the initial historical load this is much faster than per-symbol fetch.
    For daily incremental runs only the new date needs to be fetched.
    """
    jquants = JQuantsSource()
    df = jquants.fetch_earnings_bulk(start, end, cache_dir=cache_dir)

    if df.is_empty():
        logger.warning("bulk earnings: no data returned")
        return

    # Optionally restrict to known universe symbols
    if universe_filter:
        df = df.filter(df["symbol"].is_in(universe_filter))

    with Repository() as repo:
        n = repo.upsert_earnings(df)
        logger.info(
            f"bulk earnings: upserted {n} rows "
            f"({df['symbol'].n_unique()} symbols, {start} to {end})"
        )


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
    parser.add_argument(
        "--bulk-earnings",
        action="store_true",
        help="Fetch JP earnings in bulk via J-Quants get_fin_summary_range (fast, cached)",
    )
    parser.add_argument(
        "--earnings-cache-dir",
        default="data/jquants_cache",
        help="Cache directory for J-Quants bulk download (default: data/jquants_cache)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate data without writing to the database",
    )
    args = parser.parse_args()

    _start = date.fromisoformat(args.start) if args.start else None
    _end = date.fromisoformat(args.end) if args.end else date.today()

    if args.bulk_earnings:
        _bulk_start = _start or date.fromisoformat("2019-01-01")
        jp_syms = load_universe("JP")["symbol"].to_list()
        run_bulk_earnings(
            start=_bulk_start,
            end=_end,
            cache_dir=args.earnings_cache_dir,
            universe_filter=jp_syms,
        )
    else:
        run_update(
            market=args.market,
            start=_start,
            end=_end,
            with_earnings=args.with_earnings,
            dry_run=args.dry_run,
        )
