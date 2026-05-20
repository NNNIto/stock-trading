"""Tests for universe.py"""

import polars as pl
import pytest

from src.data.universe import (
    get_all_symbols,
    get_symbols,
    load_all_symbols,
    load_universe,
    normalize_symbol,
)


def test_load_universe_jp():
    df = load_universe("JP")
    assert isinstance(df, pl.DataFrame)
    assert df.height > 0
    assert set(["symbol", "name", "sector", "market"]).issubset(df.columns)
    assert df["market"].unique().to_list() == ["JP"]


def test_load_universe_us():
    df = load_universe("US")
    assert isinstance(df, pl.DataFrame)
    assert df.height > 0
    assert df["market"].unique().to_list() == ["US"]


def test_load_universe_invalid_market():
    with pytest.raises(ValueError, match="Unknown market"):
        load_universe("XX")


def test_load_all_symbols():
    df = load_all_symbols()
    assert isinstance(df, pl.DataFrame)
    jp_count = df.filter(pl.col("market") == "JP").height
    us_count = df.filter(pl.col("market") == "US").height
    assert jp_count > 0
    assert us_count > 0


def test_get_symbols_jp():
    syms = get_symbols("JP")
    assert isinstance(syms, list)
    assert len(syms) > 0
    # Japanese stocks should have .T suffix
    assert all(".T" in s for s in syms)


def test_get_symbols_us():
    syms = get_symbols("US")
    assert isinstance(syms, list)
    assert len(syms) > 0
    assert "AAPL" in syms


def test_get_all_symbols():
    all_syms = get_all_symbols()
    jp_syms = get_symbols("JP")
    us_syms = get_symbols("US")
    assert set(jp_syms + us_syms) == set(all_syms)


def test_normalize_symbol_jp_without_suffix():
    result = normalize_symbol("7203", "JP")
    assert result == "7203.T"


def test_normalize_symbol_jp_with_suffix():
    result = normalize_symbol("7203.T", "JP")
    assert result == "7203.T"


def test_normalize_symbol_us():
    result = normalize_symbol("AAPL", "US")
    assert result == "AAPL"
