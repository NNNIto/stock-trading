"""Technical indicator calculations using pandas-ta."""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]
import polars as pl

from src.utils.logger import get_logger

logger = get_logger()


def _to_pandas(df: pl.DataFrame) -> pd.DataFrame:
    return df.to_pandas()


def _to_polars(df: pd.DataFrame) -> pl.DataFrame:
    return pl.from_pandas(df)


def add_indicators(df: pl.DataFrame) -> pl.DataFrame:
    """Compute all required technical indicators for a single symbol's OHLCV DataFrame.

    Input must have columns: date, open, high, low, close, adj_close, volume.
    Output adds indicator columns; rows with insufficient history will have nulls.

    'close' is overwritten with adj_close so that all price comparisons downstream
    (MA crossovers, stop-loss checks, etc.) operate on a consistent adjusted basis.
    """
    if df.is_empty() or df.height < 5:
        return df

    pdf = _to_pandas(df)
    if "date" in pdf.columns:
        pdf = pdf.set_index("date")

    raw_close = pdf["close"]
    adj_close = pdf["adj_close"] if "adj_close" in pdf.columns else pdf["close"]
    high = pdf["high"]
    low = pdf["low"]
    volume = pdf["volume"]

    # Adjustment ratio: scale raw OHLC prices to the split/dividend-adjusted basis.
    # Where raw_close is 0 (shouldn't happen for valid data), fall back to ratio=1.
    adj_ratio = (adj_close / raw_close).replace([float("inf"), float("-inf")], 1.0).fillna(1.0)
    adj_high = high * adj_ratio

    # Normalize close to adj_close so scenarios compare prices on the same adjusted scale.
    pdf["close"] = adj_close
    close = adj_close

    # Moving averages
    pdf["ma_20"] = ta.sma(close, length=20)
    pdf["ma_50"] = ta.sma(close, length=50)
    pdf["ma_200"] = ta.sma(close, length=200)

    # MA direction: positive = upward
    pdf["ma_20_slope"] = pdf["ma_20"].diff(10)
    pdf["ma_200_slope"] = pdf["ma_200"].diff(20)
    pdf["ma_50_slope"] = pdf["ma_50"].diff(10)

    # RSI
    pdf["rsi_14"] = ta.rsi(close, length=14)
    pdf["rsi_2"] = ta.rsi(close, length=2)

    # ATR
    atr_df = ta.atr(high, low, close, length=14)
    pdf["atr_14"] = atr_df

    # Volume moving average
    pdf["vol_ma_20"] = ta.sma(volume, length=20)

    # Returns
    pdf["ret_5d"] = close.pct_change(5)
    pdf["ret_6m"] = close.pct_change(126)

    # 52-week (252 day) high of adjusted daily high prices.
    # Using actual intraday highs (scaled by adj_ratio) rather than adj_close rolling max
    # to correctly identify breakout levels as seen on adjusted-price charts.
    pdf["high_252d"] = adj_high.rolling(252).max()

    # Volume ratio vs 20-day average
    pdf["vol_ratio_20"] = volume / pdf["vol_ma_20"]

    pdf = pdf.reset_index()
    result = _to_polars(pdf)
    if "date" in result.columns and result["date"].dtype != pl.Date:
        result = result.with_columns(pl.col("date").cast(pl.Date))
    return result


def add_indicators_batch(df: pl.DataFrame) -> pl.DataFrame:
    """Apply add_indicators for each symbol in a combined DataFrame."""
    if "symbol" not in df.columns:
        return add_indicators(df)

    parts = []
    for sym in df["symbol"].unique().to_list():
        sub = df.filter(pl.col("symbol") == sym).sort("date")
        parts.append(add_indicators(sub))

    if not parts:
        return df
    return pl.concat(parts, how="diagonal")
