"""Symbol universe management for Nikkei225 and S&P500."""

from __future__ import annotations

from pathlib import Path

import polars as pl

_UNIVERSE_DIR = Path(__file__).parent.parent.parent / "config" / "universe"


def load_universe(market: str) -> pl.DataFrame:
    """Load symbol universe CSV for 'JP' or 'US'.

    Returns DataFrame with columns: symbol, name, sector, market
    """
    if market == "JP":
        path = _UNIVERSE_DIR / "nikkei225.csv"
    elif market == "US":
        path = _UNIVERSE_DIR / "sp500.csv"
    else:
        raise ValueError(f"Unknown market: {market}. Use 'JP' or 'US'.")

    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")

    return pl.read_csv(path)


def load_all_symbols() -> pl.DataFrame:
    """Load combined JP + US universe."""
    jp = load_universe("JP")
    us = load_universe("US")
    return pl.concat([jp, us])


def get_symbols(market: str) -> list[str]:
    """Return list of symbol strings for given market."""
    df = load_universe(market)
    return df["symbol"].to_list()


def get_all_symbols() -> list[str]:
    """Return all symbols across JP and US markets."""
    df = load_all_symbols()
    return df["symbol"].to_list()


def normalize_symbol(symbol: str, market: str) -> str:
    """Normalize symbol to yfinance-compatible format.

    Japanese stocks use .T suffix; US stocks are used as-is.
    """
    if market == "JP" and not symbol.endswith(".T"):
        return f"{symbol}.T"
    return symbol
