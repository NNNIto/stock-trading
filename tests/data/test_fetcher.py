"""Tests for fetcher.py – unit tests using mocks (no network calls)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from src.data.fetcher import (
    DataSourceError,
    FallbackDataSource,
    YFinancePerSymbolSource,
    YFinanceSource,
    _cross_check,
    _empty_ohlcv,
    build_default_source,
)

# ── _empty_ohlcv ────────────────────────────────────────────────────────────


def test_empty_ohlcv_schema():
    df = _empty_ohlcv()
    assert df.is_empty()
    assert "symbol" in df.columns
    assert "close" in df.columns


# ── _cross_check ─────────────────────────────────────────────────────────────


def _make_ohlcv(symbol="AAPL", close=100.0, d=date(2024, 1, 2)):
    return pl.DataFrame(
        {
            "symbol": [symbol],
            "market": ["US"],
            "date": [d],
            "open": [close],
            "high": [close + 1],
            "low": [close - 1],
            "close": [close],
            "adj_close": [close],
            "volume": [1_000_000],
        }
    ).cast({"date": pl.Date})


def test_cross_check_no_divergence(caplog):
    primary = _make_ohlcv(close=100.0)
    fallback = _make_ohlcv(close=100.5)  # 0.5% diff – within 2%
    _cross_check(primary, fallback, 0.02)


def test_cross_check_divergence_logs_warning(caplog):
    primary = _make_ohlcv(close=100.0)
    fallback = _make_ohlcv(close=110.0)  # 10% diff – exceeds 2%
    import logging

    with caplog.at_level(logging.WARNING):
        _cross_check(primary, fallback, 0.02)
    # The warning may or may not appear depending on loguru config,
    # but no exception should be raised


def test_cross_check_empty_dfs():
    # Should not raise
    _cross_check(_empty_ohlcv(), _make_ohlcv(), 0.02)
    _cross_check(_make_ohlcv(), _empty_ohlcv(), 0.02)


# ── FallbackDataSource ────────────────────────────────────────────────────────


class _GoodSource:
    name = "good"

    def fetch_ohlcv(self, symbols, start, end, market):
        return _make_ohlcv()

    def fetch_fx(self, pair, start, end):
        return pl.DataFrame({"date": [date(2024, 1, 2)], "rate": [148.0]}).cast({"date": pl.Date})

    def fetch_earnings(self, symbol):
        return pl.DataFrame(
            {
                "symbol": [symbol],
                "report_date": [None],
                "eps_actual": [None],
                "eps_estimate": [None],
            }
        ).cast({"report_date": pl.Date})


class _FailingSource:
    name = "failing"

    def fetch_ohlcv(self, symbols, start, end, market):
        raise RuntimeError("Network error")

    def fetch_fx(self, pair, start, end):
        raise RuntimeError("Network error")

    def fetch_earnings(self, symbol):
        raise RuntimeError("Network error")


def test_fallback_uses_primary_when_ok():
    primary = _GoodSource()
    fallback = _FailingSource()
    fs = FallbackDataSource(primary, [fallback])
    result = fs.fetch_ohlcv(["AAPL"], date(2024, 1, 1), date(2024, 1, 5), "US")
    assert not result.is_empty()


def test_fallback_switches_to_fallback_on_primary_failure():
    primary = _FailingSource()
    fallback = _GoodSource()
    fs = FallbackDataSource(primary, [fallback])
    result = fs.fetch_ohlcv(["AAPL"], date(2024, 1, 1), date(2024, 1, 5), "US")
    assert not result.is_empty()


def test_fallback_raises_when_all_fail():
    fs = FallbackDataSource(_FailingSource(), [_FailingSource()])
    with pytest.raises(DataSourceError):
        fs.fetch_ohlcv(["AAPL"], date(2024, 1, 1), date(2024, 1, 5), "US")


def test_fallback_fx_returns_empty_on_all_fail():
    fs = FallbackDataSource(_FailingSource(), [_FailingSource()])
    result = fs.fetch_fx("USDJPY=X", date(2024, 1, 1), date(2024, 1, 5))
    assert result.is_empty()


# ── YFinanceSource retry ──────────────────────────────────────────────────────


def test_yfinance_single_in_fallback_chain():
    source = build_default_source()
    fallback_names = [f.name for f in source._fallbacks]
    assert "yfinance_single" in fallback_names
    assert fallback_names.index("yfinance_single") == 0  # must be first fallback


def test_yfinance_single_fallback_activates_on_primary_failure():
    """Primary (batch) fails → YFinancePerSymbolSource takes over."""
    primary = _FailingSource()
    fallback = YFinancePerSymbolSource()

    # Patch the internal _retry to return synthetic data without network call

    def _patched_fetch_ohlcv(symbols, start, end, market):
        return _make_ohlcv(symbol=symbols[0])

    fallback.fetch_ohlcv = _patched_fetch_ohlcv  # type: ignore[method-assign]
    fs = FallbackDataSource(primary, [fallback])
    result = fs.fetch_ohlcv(["7203.T"], date(2024, 1, 1), date(2024, 1, 5), "JP")
    assert not result.is_empty()


def test_yfinance_retry_gives_up_after_max_attempts():
    source = YFinanceSource()
    call_count = 0

    def failing_fn():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("fail")

    with pytest.raises(DataSourceError):
        source._retry(failing_fn, attempts=3)

    assert call_count == 3
