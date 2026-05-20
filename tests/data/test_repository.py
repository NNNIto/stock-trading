"""Tests for repository.py (uses in-memory DuckDB)."""

from datetime import date

import polars as pl
import pytest

from src.data.repository import Repository


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "test.duckdb"
    r = Repository(db_path=db)
    yield r
    r.close()


def _ohlcv_row(symbol="AAPL", d=date(2024, 1, 2), market="US"):
    return {
        "symbol": symbol,
        "market": market,
        "date": d,
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 103.0,
        "adj_close": 103.0,
        "volume": 1_000_000,
    }


def test_upsert_and_query_ohlcv(repo):
    df = pl.DataFrame([_ohlcv_row()])
    n = repo.upsert_ohlcv(df)
    assert n == 1

    result = repo.query_ohlcv(symbols=["AAPL"])
    assert result.height == 1
    assert result["close"][0] == pytest.approx(103.0)


def test_upsert_idempotent(repo):
    df = pl.DataFrame([_ohlcv_row()])
    repo.upsert_ohlcv(df)
    repo.upsert_ohlcv(df)  # second upsert should not duplicate

    result = repo.query_ohlcv(symbols=["AAPL"])
    assert result.height == 1


def test_upsert_updates_existing(repo):
    df1 = pl.DataFrame([_ohlcv_row()])
    repo.upsert_ohlcv(df1)

    updated = _ohlcv_row()
    updated["close"] = 200.0
    repo.upsert_ohlcv(pl.DataFrame([updated]))

    result = repo.query_ohlcv(symbols=["AAPL"])
    assert result["close"][0] == pytest.approx(200.0)


def test_query_ohlcv_date_filter(repo):
    rows = [
        _ohlcv_row(d=date(2024, 1, 2)),
        _ohlcv_row(d=date(2024, 1, 3)),
        _ohlcv_row(d=date(2024, 1, 4)),
    ]
    repo.upsert_ohlcv(pl.DataFrame(rows))

    result = repo.query_ohlcv(symbols=["AAPL"], start="2024-01-03")
    assert result.height == 2


def test_query_ohlcv_multiple_symbols(repo):
    rows = [
        _ohlcv_row(symbol="AAPL"),
        _ohlcv_row(symbol="MSFT"),
    ]
    repo.upsert_ohlcv(pl.DataFrame(rows))

    result = repo.query_ohlcv(symbols=["AAPL", "MSFT"])
    assert result.height == 2


def test_upsert_earnings(repo):
    df = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "report_date": [date(2024, 1, 25)],
            "eps_actual": [2.18],
            "eps_estimate": [2.10],
        }
    ).cast({"report_date": pl.Date})
    n = repo.upsert_earnings(df)
    assert n == 1

    result = repo.query_earnings("AAPL")
    assert result.height == 1
    assert result["eps_actual"][0] == pytest.approx(2.18)


def test_upsert_fx(repo):
    fx_df = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "rate": [148.5, 149.0],
        }
    ).cast({"date": pl.Date})
    n = repo.upsert_fx("USDJPY", fx_df)
    assert n == 2

    result = repo.query_fx("USDJPY")
    assert result.height == 2


def test_get_symbol_count(repo):
    rows = [_ohlcv_row(symbol="AAPL"), _ohlcv_row(symbol="MSFT")]
    repo.upsert_ohlcv(pl.DataFrame(rows))
    assert repo.get_symbol_count() == 2


def test_get_date_range(repo):
    rows = [
        _ohlcv_row(d=date(2024, 1, 2)),
        _ohlcv_row(d=date(2024, 1, 5)),
    ]
    repo.upsert_ohlcv(pl.DataFrame(rows))
    start, end = repo.get_date_range("AAPL")
    assert start == "2024-01-02"
    assert end == "2024-01-05"


def test_context_manager(tmp_path):
    db = tmp_path / "ctx.duckdb"
    with Repository(db_path=db) as repo:
        df = pl.DataFrame([_ohlcv_row()])
        repo.upsert_ohlcv(df)
        assert repo.get_symbol_count() == 1


def test_upsert_empty_df(repo):
    empty = pl.DataFrame(
        {
            "symbol": [],
            "market": [],
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
            "date": pl.Date,
        }
    )
    n = repo.upsert_ohlcv(empty)
    assert n == 0
