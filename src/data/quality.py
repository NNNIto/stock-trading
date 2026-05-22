"""Data quality checks for OHLCV data."""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from src.utils.logger import get_logger

logger = get_logger()

# Thresholds
MAX_DAILY_RETURN = 0.30  # flag if abs daily return > 30%
MIN_PRICE = 0.01  # flag if price < 1 yen / 1 cent


@dataclass
class QualityReport:
    symbol: str
    total_rows: int
    missing_rows: int
    outlier_rows: int
    inconsistent_rows: int
    issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.missing_rows == 0 and self.outlier_rows == 0 and self.inconsistent_rows == 0

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.symbol}: total={self.total_rows}, "
            f"missing={self.missing_rows}, outliers={self.outlier_rows}, "
            f"inconsistent={self.inconsistent_rows}"
        )


def check_missing(df: pl.DataFrame, symbol: str) -> int:
    """Return count of rows with any null in OHLCV columns."""
    ohlcv_cols = ["open", "high", "low", "close", "adj_close", "volume"]
    existing = [c for c in ohlcv_cols if c in df.columns]
    if not existing:
        return 0
    null_mask = pl.lit(False)
    for col in existing:
        null_mask = null_mask | pl.col(col).is_null()
    n = df.filter(null_mask).height
    if n > 0:
        logger.warning(f"quality: {symbol} has {n} rows with null values")
    return n


def check_outliers(df: pl.DataFrame, symbol: str) -> int:
    """Return count of rows with suspicious price movements (>50% daily change) or negative prices."""
    if "close" not in df.columns or df.height < 2:
        return 0

    df_sorted = df.sort("date") if "date" in df.columns else df

    # Calculate daily return
    df_with_ret = df_sorted.with_columns(
        (pl.col("close") / pl.col("close").shift(1) - 1).alias("_daily_ret")
    )

    outlier_mask = (
        (pl.col("_daily_ret").abs() > MAX_DAILY_RETURN)
        | (pl.col("close") < MIN_PRICE)
        | (pl.col("open") < MIN_PRICE)
        | (pl.col("high") < MIN_PRICE)
        | (pl.col("low") < MIN_PRICE)
    )
    n = df_with_ret.filter(outlier_mask).height
    if n > 0:
        logger.warning(f"quality: {symbol} has {n} outlier rows")
    return n


def check_consistency(df: pl.DataFrame, symbol: str) -> int:
    """Return count of rows violating OHLCV consistency rules.

    Rules: high >= low, high >= close, high >= open, low <= close, low <= open.
    """
    if not all(c in df.columns for c in ["open", "high", "low", "close"]):
        return 0

    bad = df.filter(
        (pl.col("high") < pl.col("low"))
        | (pl.col("high") < pl.col("close"))
        | (pl.col("high") < pl.col("open"))
        | (pl.col("low") > pl.col("close"))
        | (pl.col("low") > pl.col("open"))
    )
    n = bad.height
    if n > 0:
        logger.warning(f"quality: {symbol} has {n} inconsistent OHLCV rows")
    return n


def run_quality_check(df: pl.DataFrame, symbol: str) -> QualityReport:
    """Run all quality checks on a single symbol's DataFrame."""
    report = QualityReport(
        symbol=symbol,
        total_rows=df.height,
        missing_rows=check_missing(df, symbol),
        outlier_rows=check_outliers(df, symbol),
        inconsistent_rows=check_consistency(df, symbol),
    )
    if not report.passed:
        report.issues.append(str(report))
    logger.info(str(report))
    return report


def clean_ohlcv(df: pl.DataFrame) -> pl.DataFrame:
    """Remove rows that fail consistency checks; forward-fill isolated nulls."""
    if df.is_empty():
        return df

    ohlcv_cols = ["open", "high", "low", "close", "adj_close", "volume"]
    existing = [c for c in ohlcv_cols if c in df.columns]

    # Drop rows where core price columns are null
    null_mask = pl.lit(False)
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            null_mask = null_mask | pl.col(col).is_null()
    df = df.filter(~null_mask)

    # Drop inconsistent rows
    if all(c in df.columns for c in ["open", "high", "low", "close"]):
        df = df.filter(
            (pl.col("high") >= pl.col("low"))
            & (pl.col("high") >= pl.col("close"))
            & (pl.col("high") >= pl.col("open"))
            & (pl.col("low") <= pl.col("close"))
            & (pl.col("low") <= pl.col("open"))
        )

    # Forward-fill volume nulls
    if "volume" in existing:
        df = df.with_columns(pl.col("volume").forward_fill())

    return df


def run_batch_quality_check(df: pl.DataFrame) -> list[QualityReport]:
    """Run quality checks for all symbols in a combined DataFrame."""
    if "symbol" not in df.columns:
        return [run_quality_check(df, "unknown")]

    reports = []
    for sym in df["symbol"].unique().to_list():
        sub = df.filter(pl.col("symbol") == sym)
        reports.append(run_quality_check(sub, sym))
    return reports
