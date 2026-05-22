"""Tests for universe.py"""

from datetime import date

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
    assert df.height == 225
    assert set(["symbol", "name", "sector", "market"]).issubset(df.columns)
    assert df["market"].unique().to_list() == ["JP"]
    # removed_date column must NOT appear in result
    assert "removed_date" not in df.columns


def test_load_universe_us():
    df = load_universe("US")
    assert isinstance(df, pl.DataFrame)
    assert df.height > 0
    assert df["market"].unique().to_list() == ["US"]
    assert "removed_date" not in df.columns


def test_load_universe_invalid_market():
    with pytest.raises(ValueError, match="Unknown market"):
        load_universe("XX")


def test_load_all_symbols():
    df = load_all_symbols()
    assert isinstance(df, pl.DataFrame)
    jp_count = df.filter(pl.col("market") == "JP").height
    us_count = df.filter(pl.col("market") == "US").height
    assert jp_count == 225
    assert us_count > 0


def test_get_symbols_jp():
    syms = get_symbols("JP")
    assert isinstance(syms, list)
    assert len(syms) == 225
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


# --- as_of 生存者バイアス対策テスト ---


def test_load_universe_as_of_excludes_recently_added():
    # 4385.T(メルカリ)は2026-04-01追加 → それ以前は含まれない
    before = load_universe("JP", as_of=date(2026, 3, 31))
    after = load_universe("JP", as_of=date(2026, 4, 1))
    before_syms = set(before["symbol"].to_list())
    after_syms = set(after["symbol"].to_list())
    assert "4385.T" not in before_syms
    assert "4385.T" in after_syms


def test_load_universe_as_of_includes_recently_removed():
    # 7003.T(三井E&S)は2026-04-01除外 → それ以前は含まれる
    before = load_universe("JP", as_of=date(2026, 3, 31))
    after = load_universe("JP", as_of=date(2026, 4, 1))
    before_syms = set(before["symbol"].to_list())
    after_syms = set(after["symbol"].to_list())
    assert "7003.T" in before_syms
    assert "7003.T" not in after_syms


def test_load_universe_as_of_after_change():
    # as_of=2026-04-02: 4月入替後は現在と同じ225銘柄
    after = load_universe("JP", as_of=date(2026, 4, 2))
    assert after.height == 225


def test_get_symbols_as_of_differs_from_current():
    # 過去日付と現在では銘柄セットが異なる（4月入替が反映される）
    syms_current = get_symbols("JP")
    syms_past = get_symbols("JP", as_of=date(2026, 3, 31))
    assert len(syms_current) == 225
    # 過去は追加3銘柄を含まず、除外29銘柄を含む（除外日が近似値のため現在より多い）
    assert set(syms_current) != set(syms_past)
    assert "4385.T" not in syms_past  # 2026-04-01追加なので3月末は含まれない
