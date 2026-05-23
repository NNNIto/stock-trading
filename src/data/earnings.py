"""Earnings enrichment: join earnings data onto OHLCV+indicator DataFrame.

Responsibility: the caller (batch script / backtest engine) calls
enrich_with_earnings() after add_indicators() to add earnings-derived
columns that S4 (and future scenarios) depend on.
"""

from __future__ import annotations

import bisect
from datetime import date

import polars as pl

from src.utils.logger import get_logger

logger = get_logger()

# Columns added by enrich_with_earnings()
EARNINGS_COLS = ["is_earnings_day", "eps_surprise_pct", "next_report_date"]


def enrich_with_earnings(df: pl.DataFrame, earnings: pl.DataFrame) -> pl.DataFrame:
    """Add earnings-derived columns to an OHLCV+indicators DataFrame.

    Added columns:
    - is_earnings_day  (Boolean) : True when date matches a report_date
    - eps_surprise_pct (Float64) : EPS surprise on earnings days, null otherwise
    - next_report_date (Date)    : nearest future report_date > row date, null if none

    Args:
        df:       Per-symbol or multi-symbol OHLCV DataFrame with 'symbol' and 'date'.
        earnings: DataFrame with columns 'symbol', 'report_date', 'eps_surprise_pct'.
                  Typically from Repository.query_earnings() across all symbols.

    Returns:
        df with the three earnings columns appended.
    """
    if "symbol" not in df.columns or "date" not in df.columns:
        raise ValueError("df must contain 'symbol' and 'date' columns")

    # Remove any pre-existing earnings columns to avoid conflicts on re-enrichment
    df = df.drop([c for c in EARNINGS_COLS if c in df.columns])

    if earnings.is_empty():
        logger.warning("enrich_with_earnings: earnings DataFrame is empty — filling with nulls")
        return _add_null_earnings_cols(df)

    # ── 1. is_earnings_day + eps_surprise_pct (left-join on symbol, date) ────
    earnings_flag = (
        earnings.filter(pl.col("report_date").is_not_null())
        .select(
            pl.col("symbol"),
            pl.col("report_date").alias("date"),
            pl.lit(True).alias("is_earnings_day"),
            pl.col("eps_surprise_pct"),
        )
        .unique(subset=["symbol", "date"])
    )

    df = df.join(earnings_flag, on=["symbol", "date"], how="left").with_columns(
        pl.col("is_earnings_day").fill_null(False)
    )

    # ── 2. next_report_date (per-symbol binary search) ───────────────────────
    parts: list[pl.DataFrame] = []
    for sym in df["symbol"].unique().to_list():
        sub = df.filter(pl.col("symbol") == sym)
        sym_earnings = earnings.filter(pl.col("symbol") == sym, pl.col("report_date").is_not_null())

        if sym_earnings.is_empty():
            sub = sub.with_columns(pl.lit(None).cast(pl.Date).alias("next_report_date"))
            parts.append(sub)
            continue

        report_dates: list[date] = sorted(sym_earnings["report_date"].to_list())
        row_dates: list[date] = sub["date"].to_list()

        next_reports: list[date | None] = []
        for d in row_dates:
            idx = bisect.bisect_right(report_dates, d)
            next_reports.append(report_dates[idx] if idx < len(report_dates) else None)

        parts.append(sub.with_columns(pl.Series("next_report_date", next_reports, dtype=pl.Date)))

    result = pl.concat(parts, how="diagonal")
    logger.info(
        f"enrich_with_earnings: enriched {result.height} rows across "
        f"{result['symbol'].n_unique()} symbols"
    )
    return result


def query_earnings_for_symbols(repo: object, symbols: list[str]) -> pl.DataFrame:
    """Fetch earnings for a list of symbols from the repository.

    Convenience wrapper so callers don't need to loop themselves.
    """
    from src.data.repository import Repository

    assert isinstance(repo, Repository)
    frames: list[pl.DataFrame] = []
    for sym in symbols:
        df = repo.query_earnings(sym)
        if not df.is_empty():
            frames.append(df)
    if not frames:
        return pl.DataFrame(
            {
                "symbol": [],
                "report_date": [],
                "eps_actual": [],
                "eps_estimate": [],
                "surprise_pct": [],
            }
        ).cast(
            {
                "report_date": pl.Date,
                "eps_actual": pl.Float64,
                "eps_estimate": pl.Float64,
                "surprise_pct": pl.Float64,
            }
        )  # type: ignore[arg-type]

    combined = pl.concat(frames, how="diagonal")
    # Normalise column name: the DB stores 'surprise_pct', S4 expects 'eps_surprise_pct'
    if "surprise_pct" in combined.columns and "eps_surprise_pct" not in combined.columns:
        combined = combined.rename({"surprise_pct": "eps_surprise_pct"})
    return combined


# ── helpers ───────────────────────────────────────────────────────────────────


def _add_null_earnings_cols(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(False).alias("is_earnings_day"),
        pl.lit(None).cast(pl.Float64).alias("eps_surprise_pct"),
        pl.lit(None).cast(pl.Date).alias("next_report_date"),
    )
