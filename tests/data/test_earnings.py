"""Tests for src/data/earnings.py"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from src.data.earnings import EARNINGS_COLS, enrich_with_earnings

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_ohlcv(symbol: str, dates: list[date]) -> pl.DataFrame:
    n = len(dates)
    return pl.DataFrame(
        {
            "symbol": [symbol] * n,
            "date": dates,
            "close": [100.0] * n,
        }
    )


def _make_earnings(
    symbol: str, report_dates: list[date], surprises: list[float | None]
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * len(report_dates),
            "report_date": report_dates,
            "eps_surprise_pct": surprises,
        }
    ).cast({"report_date": pl.Date, "eps_surprise_pct": pl.Float64})  # type: ignore[arg-type]


# ── is_earnings_day ───────────────────────────────────────────────────────────


def test_is_earnings_day_true_on_report_date() -> None:
    dates = [date(2024, 1, i + 1) for i in range(5)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 3)], [0.10])

    result = enrich_with_earnings(ohlcv, earnings)

    flags = result.sort("date")["is_earnings_day"].to_list()
    assert flags == [False, False, True, False, False]


def test_is_earnings_day_false_on_non_report_dates() -> None:
    dates = [date(2024, 1, i + 1) for i in range(5)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 2, 1)], [0.05])  # outside range

    result = enrich_with_earnings(ohlcv, earnings)
    assert result["is_earnings_day"].to_list() == [False] * 5


# ── eps_surprise_pct ──────────────────────────────────────────────────────────


def test_eps_surprise_populated_on_earnings_day() -> None:
    dates = [date(2024, 1, i + 1) for i in range(5)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 2)], [0.15])

    result = enrich_with_earnings(ohlcv, earnings).sort("date")
    surprises = result["eps_surprise_pct"].to_list()

    assert surprises[1] == pytest.approx(0.15)
    assert all(v is None for v in [surprises[0]] + surprises[2:])


def test_eps_surprise_null_when_no_eps_data() -> None:
    dates = [date(2024, 1, i + 1) for i in range(3)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 2)], [None])

    result = enrich_with_earnings(ohlcv, earnings).sort("date")
    assert result["eps_surprise_pct"][1] is None


# ── next_report_date ──────────────────────────────────────────────────────────


def test_next_report_date_points_to_future_earnings() -> None:
    dates = [date(2024, 1, i + 1) for i in range(5)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 4)], [0.10])

    result = enrich_with_earnings(ohlcv, earnings).sort("date")
    nrd = result["next_report_date"].to_list()

    # Rows before 2024-01-04 should point to 2024-01-04
    assert nrd[0] == date(2024, 1, 4)
    assert nrd[1] == date(2024, 1, 4)
    assert nrd[2] == date(2024, 1, 4)
    # On and after the report date, no next report
    assert nrd[3] is None
    assert nrd[4] is None


def test_next_report_date_null_after_last_earnings() -> None:
    dates = [date(2024, 1, 10), date(2024, 1, 11)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 5)], [0.05])

    result = enrich_with_earnings(ohlcv, earnings)
    assert result["next_report_date"].to_list() == [None, None]


def test_next_report_date_multiple_future_earnings() -> None:
    dates = [date(2024, 1, 1), date(2024, 4, 1), date(2024, 7, 1)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings(
        "AAPL",
        [date(2024, 1, 31), date(2024, 4, 30)],
        [0.10, 0.05],
    )

    result = enrich_with_earnings(ohlcv, earnings).sort("date")
    nrd = result["next_report_date"].to_list()

    assert nrd[0] == date(2024, 1, 31)  # nearest upcoming report
    assert nrd[1] == date(2024, 4, 30)  # next report after April 1
    assert nrd[2] is None  # no report after July 1


# ── multi-symbol ──────────────────────────────────────────────────────────────


def test_multi_symbol_enrichment() -> None:
    dates = [date(2024, 1, i + 1) for i in range(3)]
    aapl = _make_ohlcv("AAPL", dates)
    msft = _make_ohlcv("MSFT", dates)
    ohlcv = pl.concat([aapl, msft])

    earnings = pl.concat(
        [
            _make_earnings("AAPL", [date(2024, 1, 2)], [0.10]),
            _make_earnings("MSFT", [date(2024, 1, 3)], [0.05]),
        ]
    )

    result = enrich_with_earnings(ohlcv, earnings)

    aapl_res = result.filter(pl.col("symbol") == "AAPL").sort("date")
    msft_res = result.filter(pl.col("symbol") == "MSFT").sort("date")

    assert aapl_res["is_earnings_day"].to_list() == [False, True, False]
    assert msft_res["is_earnings_day"].to_list() == [False, False, True]
    # AAPL next_report before Jan 2 → Jan 2; on/after → None
    assert aapl_res["next_report_date"][0] == date(2024, 1, 2)
    assert aapl_res["next_report_date"][1] is None


def test_symbol_with_no_earnings_gets_null_cols() -> None:
    dates = [date(2024, 1, i + 1) for i in range(3)]
    ohlcv = _make_ohlcv("NVDA", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 2)], [0.10])  # different symbol

    result = enrich_with_earnings(ohlcv, earnings)

    assert result["is_earnings_day"].to_list() == [False, False, False]
    assert result["eps_surprise_pct"].to_list() == [None, None, None]
    assert result["next_report_date"].to_list() == [None, None, None]


# ── edge cases ────────────────────────────────────────────────────────────────


def test_empty_earnings_adds_null_cols() -> None:
    dates = [date(2024, 1, i + 1) for i in range(3)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = pl.DataFrame({"symbol": [], "report_date": [], "eps_surprise_pct": []}).cast(
        {"report_date": pl.Date, "eps_surprise_pct": pl.Float64}
    )  # type: ignore[arg-type]

    result = enrich_with_earnings(ohlcv, earnings)

    for col in EARNINGS_COLS:
        assert col in result.columns

    assert result["is_earnings_day"].to_list() == [False, False, False]


def test_re_enrichment_does_not_duplicate_cols() -> None:
    """Calling enrich_with_earnings twice should not add duplicate columns."""
    dates = [date(2024, 1, i + 1) for i in range(3)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 2)], [0.10])

    result = enrich_with_earnings(ohlcv, earnings)
    result2 = enrich_with_earnings(result, earnings)

    for col in EARNINGS_COLS:
        assert result2.columns.count(col) == 1


def test_output_columns_present() -> None:
    dates = [date(2024, 1, i + 1) for i in range(2)]
    ohlcv = _make_ohlcv("AAPL", dates)
    earnings = _make_earnings("AAPL", [date(2024, 1, 1)], [0.0])

    result = enrich_with_earnings(ohlcv, earnings)

    for col in EARNINGS_COLS:
        assert col in result.columns


def test_missing_symbol_or_date_raises() -> None:
    df_no_symbol = pl.DataFrame({"date": [date(2024, 1, 1)], "close": [100.0]})
    earnings = _make_earnings("AAPL", [date(2024, 1, 1)], [0.0])
    with pytest.raises(ValueError, match="symbol"):
        enrich_with_earnings(df_no_symbol, earnings)
