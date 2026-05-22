"""Symbol universe management for Nikkei225 and S&P500.

Survivorship-bias status (as of 2026-05):
  Nikkei225: removed_date is populated for known index exits. Historical data
    for 4 delisted symbols (2651.T, 4601.T, 9613.T, 9681.T) is unavailable via
    yfinance after privatisation/delisting; their ohlcv rows are absent from the DB.
  S&P500: removed_date is not tracked (all entries have removed_date=""). Only
    current constituents are covered — full historical composition would require a
    paid data source (Bloomberg, Compustat, CRSP).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

_UNIVERSE_DIR = Path(__file__).parent.parent.parent / "config" / "universe"

_ACTIVE_COLS = ["symbol", "name", "sector", "market"]


def load_universe(market: str, as_of: date | None = None) -> pl.DataFrame:
    """Load symbol universe CSV for 'JP' or 'US'.

    Returns DataFrame with columns: symbol, name, sector, market.

    If as_of is None, returns currently active symbols (removed_date empty).
    If as_of is provided, returns symbols that were in the index on that date:
      - added_date <= as_of  (or added_date unknown)
      - removed_date > as_of (or removed_date unknown, i.e. still active)
    """
    if market == "JP":
        path = _UNIVERSE_DIR / "nikkei225.csv"
    elif market == "US":
        path = _UNIVERSE_DIR / "sp500.csv"
    else:
        raise ValueError(f"Unknown market: {market}. Use 'JP' or 'US'.")

    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")

    df = pl.read_csv(path)

    has_history = "added_date" in df.columns and "removed_date" in df.columns

    if not has_history:
        return df.select([c for c in _ACTIVE_COLS if c in df.columns])

    if as_of is None:
        df = df.filter(pl.col("removed_date").is_null() | (pl.col("removed_date") == ""))
    else:
        as_of_str = as_of.isoformat()
        added_ok = (
            pl.col("added_date").is_null()
            | (pl.col("added_date") == "")
            | (pl.col("added_date") <= as_of_str)
        )
        removed_ok = (
            pl.col("removed_date").is_null()
            | (pl.col("removed_date") == "")
            | (pl.col("removed_date") > as_of_str)
        )
        df = df.filter(added_ok & removed_ok)

    return df.select([c for c in _ACTIVE_COLS if c in df.columns])


def load_all_symbols(as_of: date | None = None) -> pl.DataFrame:
    """Load combined JP + US universe."""
    jp = load_universe("JP", as_of=as_of)
    us = load_universe("US", as_of=as_of)
    return pl.concat([jp, us])


def get_symbols(market: str, as_of: date | None = None) -> list[str]:
    """Return list of symbol strings for given market."""
    df = load_universe(market, as_of=as_of)
    return df["symbol"].to_list()


def get_all_symbols(as_of: date | None = None) -> list[str]:
    """Return all symbols across JP and US markets."""
    df = load_all_symbols(as_of=as_of)
    return df["symbol"].to_list()


def normalize_symbol(symbol: str, market: str) -> str:
    """Normalize symbol to yfinance-compatible format.

    Japanese stocks use .T suffix; US stocks are used as-is.
    """
    if market == "JP" and not symbol.endswith(".T"):
        return f"{symbol}.T"
    return symbol
