"""Tests for quality.py"""

from datetime import date

import polars as pl

from src.data.quality import (
    QualityReport,
    check_consistency,
    check_missing,
    check_outliers,
    clean_ohlcv,
    run_batch_quality_check,
    run_quality_check,
)


def _make_ohlcv(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows).cast({
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "adj_close": pl.Float64,
        "volume": pl.Int64,
    })


CLEAN_ROWS = [
    {"symbol": "AAPL", "date": date(2024, 1, 2), "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "adj_close": 103.0, "volume": 1000000},
    {"symbol": "AAPL", "date": date(2024, 1, 3), "open": 103.0, "high": 108.0, "low": 102.0, "close": 106.0, "adj_close": 106.0, "volume": 1200000},
    {"symbol": "AAPL", "date": date(2024, 1, 4), "open": 106.0, "high": 110.0, "low": 104.0, "close": 108.0, "adj_close": 108.0, "volume": 900000},
]


def test_check_missing_no_nulls():
    df = pl.DataFrame(CLEAN_ROWS)
    assert check_missing(df, "AAPL") == 0


def test_check_missing_with_null():
    rows = CLEAN_ROWS.copy()
    rows[1] = {**rows[1], "close": None}
    df = pl.DataFrame(rows)
    assert check_missing(df, "AAPL") == 1


def test_check_outliers_normal():
    df = pl.DataFrame(CLEAN_ROWS)
    assert check_outliers(df, "AAPL") == 0


def test_check_outliers_price_spike():
    rows = [
        {"symbol": "AAPL", "date": date(2024, 1, 2), "open": 100.0, "high": 105.0, "low": 99.0, "close": 100.0, "adj_close": 100.0, "volume": 1000000},
        {"symbol": "AAPL", "date": date(2024, 1, 3), "open": 100.0, "high": 200.0, "low": 99.0, "close": 200.0, "adj_close": 200.0, "volume": 1000000},  # +100% spike
    ]
    df = pl.DataFrame(rows)
    assert check_outliers(df, "AAPL") >= 1


def test_check_consistency_valid():
    df = pl.DataFrame(CLEAN_ROWS)
    assert check_consistency(df, "AAPL") == 0


def test_check_consistency_high_lt_low():
    rows = [
        {"symbol": "AAPL", "date": date(2024, 1, 2), "open": 100.0, "high": 90.0, "low": 95.0, "close": 92.0, "adj_close": 92.0, "volume": 1000000},
    ]
    df = pl.DataFrame(rows)
    assert check_consistency(df, "AAPL") == 1


def test_run_quality_check_pass():
    df = pl.DataFrame(CLEAN_ROWS)
    report = run_quality_check(df, "AAPL")
    assert report.passed
    assert report.symbol == "AAPL"
    assert report.total_rows == 3


def test_run_quality_check_fail():
    rows = [{**r} for r in CLEAN_ROWS]
    rows[0] = {**rows[0], "high": 80.0}  # high < low
    df = pl.DataFrame(rows)
    report = run_quality_check(df, "AAPL")
    assert not report.passed


def test_quality_report_str():
    r = QualityReport("TEST", 100, 0, 0, 0)
    assert "PASS" in str(r)
    r2 = QualityReport("TEST", 100, 1, 0, 0)
    assert "FAIL" in str(r2)


def test_clean_ohlcv_removes_null_rows():
    rows = CLEAN_ROWS.copy()
    null_row = {"symbol": "AAPL", "date": date(2024, 1, 5), "open": None, "high": None, "low": None, "close": None, "adj_close": None, "volume": 0}
    df = pl.DataFrame(rows + [null_row])
    cleaned = clean_ohlcv(df)
    assert cleaned.height == 3


def test_clean_ohlcv_removes_inconsistent():
    bad_row = {"symbol": "AAPL", "date": date(2024, 1, 5), "open": 100.0, "high": 80.0, "low": 90.0, "close": 85.0, "adj_close": 85.0, "volume": 1000}
    df = pl.DataFrame(CLEAN_ROWS + [bad_row])
    cleaned = clean_ohlcv(df)
    assert cleaned.height == 3


def test_run_batch_quality_check():
    rows_b = [
        {**r, "symbol": "MSFT"} for r in CLEAN_ROWS
    ]
    df = pl.DataFrame(CLEAN_ROWS + rows_b)
    reports = run_batch_quality_check(df)
    assert len(reports) == 2
    assert all(r.passed for r in reports)
