"""Tests for indicators.py"""

from datetime import date, timedelta

import polars as pl
import pytest

from src.data.indicators import add_indicators, add_indicators_batch


def _make_price_series(n: int = 300, start_price: float = 100.0) -> pl.DataFrame:
    """Generate synthetic uptrending OHLCV data."""
    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(n)]
    prices = [start_price + i * 0.1 for i in range(n)]
    return pl.DataFrame(
        {
            "symbol": ["AAPL"] * n,
            "date": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "adj_close": prices,
            "volume": [1_000_000] * n,
            "market": ["US"] * n,
        }
    )


def test_add_indicators_columns_present():
    df = _make_price_series(300)
    result = add_indicators(df)
    expected_cols = [
        "ma_20",
        "ma_50",
        "ma_200",
        "rsi_14",
        "rsi_2",
        "atr_14",
        "vol_ma_20",
        "ret_5d",
        "ret_6m",
        "high_252d",
        "vol_ratio_20",
    ]
    for col in expected_cols:
        assert col in result.columns, f"Missing column: {col}"


def test_add_indicators_ma20_correct():
    df = _make_price_series(300)
    result = add_indicators(df)
    # Last MA20 should be close to last 20 prices average
    result_pd = result.to_pandas()
    last_ma20 = result_pd["ma_20"].dropna().iloc[-1]
    last_prices = result_pd["adj_close"].iloc[-20:].mean()
    assert abs(last_ma20 - last_prices) < 1.0


def test_add_indicators_rsi_range():
    # Use oscillating prices so RSI stays well within bounds
    import math

    n = 300
    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(n)]
    prices = [100.0 + 10 * math.sin(i * 0.2) for i in range(n)]
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * n,
            "date": dates,
            "open": prices,
            "high": [p * 1.005 for p in prices],
            "low": [p * 0.995 for p in prices],
            "close": prices,
            "adj_close": prices,
            "volume": [1_000_000] * n,
            "market": ["US"] * n,
        }
    )
    result = add_indicators(df)
    rsi_vals = result["rsi_14"].drop_nulls()
    assert (rsi_vals >= 0).all()
    assert (rsi_vals <= 100.001).all()


def test_add_indicators_52w_high():
    df = _make_price_series(300)
    result = add_indicators(df)
    # high_252d is the 252-day rolling max of adjusted high prices (high * adj_ratio).
    # In _make_price_series: high = adj_close * 1.01, adj_ratio = 1.0 (close == adj_close).
    # For monotonically increasing prices the latest high is the rolling maximum.
    last_row = result.sort("date").tail(1)
    assert last_row["high_252d"][0] == pytest.approx(last_row["adj_close"][0] * 1.01, rel=1e-3)


def test_ohlc_normalized_to_adjusted_prices():
    """add_indicators normalizes all OHLC columns to adj_close basis for consistency."""
    n = 300
    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(n)]
    raw_prices = [100.0 + i * 0.1 for i in range(n)]
    adj_prices = [p * 0.5 for p in raw_prices]  # simulate 2:1 split backward adjustment
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"] * n,
            "date": dates,
            "open": raw_prices,
            "high": [p * 1.01 for p in raw_prices],
            "low": [p * 0.99 for p in raw_prices],
            "close": raw_prices,
            "adj_close": adj_prices,
            "volume": [1_000_000] * n,
            "market": ["US"] * n,
        }
    )
    result = add_indicators(df)
    non_null = result.filter(pl.col("close").is_not_null())
    # close must equal adj_close
    assert (non_null["close"] == non_null["adj_close"]).all()
    # open/high/low must also be scaled by adj_ratio (0.5 in this test)
    assert result["open"].drop_nulls().to_list()[0] == pytest.approx(raw_prices[0] * 0.5, rel=1e-6)
    assert result["high"].drop_nulls().to_list()[0] == pytest.approx(
        raw_prices[0] * 1.01 * 0.5, rel=1e-6
    )
    assert result["low"].drop_nulls().to_list()[0] == pytest.approx(
        raw_prices[0] * 0.99 * 0.5, rel=1e-6
    )


def test_high_252d_uses_adjusted_high():
    """high_252d is based on adjusted daily high prices, not adj_close rolling max."""
    df = _make_price_series(300)
    result = add_indicators(df)
    last_row = result.sort("date").tail(1)
    # high = adj_close * 1.01 in test data, so high_252d should exceed adj_close
    assert last_row["high_252d"][0] > last_row["adj_close"][0]


def test_add_indicators_empty_df():
    empty = pl.DataFrame(
        {
            "symbol": [],
            "date": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "adj_close": [],
            "volume": [],
        }
    ).cast(
        {
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "adj_close": pl.Float64,
            "volume": pl.Int64,
        }
    )
    result = add_indicators(empty)
    assert result.is_empty()


def test_add_indicators_too_few_rows():
    df = _make_price_series(3)
    result = add_indicators(df)
    # Should return unchanged (height < 5 check)
    assert result.height == 3


def test_add_indicators_batch_multiple_symbols():
    df_a = _make_price_series(300)
    df_b = _make_price_series(300, start_price=200.0).with_columns(pl.lit("MSFT").alias("symbol"))
    combined = pl.concat([df_a, df_b])
    result = add_indicators_batch(combined)
    assert "ma_20" in result.columns
    symbols = result["symbol"].unique().to_list()
    assert set(symbols) == {"AAPL", "MSFT"}


def test_vol_ratio_calculation():
    # Use 300 rows so all indicator warm-ups complete
    df = _make_price_series(300)
    result = add_indicators(df)
    # With constant volume, vol_ratio should be 1.0 after warm-up period
    last_ratio = result["vol_ratio_20"].drop_nulls().to_list()[-1]
    assert last_ratio == pytest.approx(1.0, abs=0.01)
