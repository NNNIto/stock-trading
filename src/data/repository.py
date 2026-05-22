"""DuckDB I/O layer for OHLCV, earnings, FX, and related tables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from src.utils.logger import get_logger

logger = get_logger()

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "trading.duckdb"

_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol  VARCHAR NOT NULL,
    market  VARCHAR NOT NULL,
    date    DATE    NOT NULL,
    open    DOUBLE  NOT NULL,
    high    DOUBLE  NOT NULL,
    low     DOUBLE  NOT NULL,
    close   DOUBLE  NOT NULL,
    adj_close DOUBLE NOT NULL,
    volume  BIGINT  NOT NULL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date);

CREATE TABLE IF NOT EXISTS earnings (
    symbol       VARCHAR NOT NULL,
    report_date  DATE,
    eps_actual   DOUBLE,
    eps_estimate DOUBLE,
    surprise_pct DOUBLE,
    PRIMARY KEY (symbol, report_date)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair   VARCHAR NOT NULL,
    date   DATE    NOT NULL,
    rate   DOUBLE  NOT NULL,
    PRIMARY KEY (pair, date)
);

CREATE TABLE IF NOT EXISTS universe (
    symbol  VARCHAR NOT NULL,
    market  VARCHAR NOT NULL,
    name    VARCHAR,
    sector  VARCHAR,
    PRIMARY KEY (symbol)
);
"""


class Repository:
    """DuckDB-backed repository for market data."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._path = Path(db_path) if db_path else _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self._path))
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(_DDL)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── OHLCV ────────────────────────────────────────────────────────────────

    def upsert_ohlcv(self, df: pl.DataFrame) -> int:
        """Idempotent upsert of OHLCV rows. Returns number of rows written."""
        if df.is_empty():
            return 0

        needed = ["symbol", "market", "date", "open", "high", "low", "close", "adj_close", "volume"]
        df = df.select([c for c in needed if c in df.columns])

        # Register as DuckDB relation and upsert
        self._conn.register("_ohlcv_staging", df.to_arrow())
        self._conn.execute("""
            INSERT INTO ohlcv
            SELECT symbol, market, date, open, high, low, close, adj_close, volume
            FROM _ohlcv_staging
            ON CONFLICT (symbol, date) DO UPDATE SET
                market    = excluded.market,
                open      = excluded.open,
                high      = excluded.high,
                low       = excluded.low,
                close     = excluded.close,
                adj_close = excluded.adj_close,
                volume    = excluded.volume
        """)
        self._conn.unregister("_ohlcv_staging")
        logger.info(f"upsert_ohlcv: wrote {df.height} rows")
        return df.height

    def query_ohlcv(
        self,
        symbols: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        market: str | None = None,
    ) -> pl.DataFrame:
        """Query OHLCV table with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if symbols:
            placeholders = ", ".join(["?" for _ in symbols])
            conditions.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if start:
            conditions.append("date >= ?")
            params.append(start)
        if end:
            conditions.append("date <= ?")
            params.append(end)
        if market:
            conditions.append("market = ?")
            params.append(market)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM ohlcv {where} ORDER BY symbol, date"
        result = self._conn.execute(sql, params).arrow()
        return pl.from_arrow(result)  # type: ignore[return-value]

    # ── Earnings ─────────────────────────────────────────────────────────────

    def upsert_earnings(self, df: pl.DataFrame) -> int:
        if df.is_empty():
            return 0
        # Drop rows with no report_date (placeholder rows from _empty_earnings())
        df = df.filter(pl.col("report_date").is_not_null())
        if df.is_empty():
            return 0
        # Add surprise_pct column if missing (backward compat)
        if "surprise_pct" not in df.columns:
            df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("surprise_pct"))
        self._conn.register("_earnings_staging", df.to_arrow())
        self._conn.execute("""
            INSERT INTO earnings (symbol, report_date, eps_actual, eps_estimate, surprise_pct)
            SELECT symbol, report_date, eps_actual, eps_estimate, surprise_pct
            FROM _earnings_staging
            ON CONFLICT (symbol, report_date) DO UPDATE SET
                eps_actual   = excluded.eps_actual,
                eps_estimate = excluded.eps_estimate,
                surprise_pct = excluded.surprise_pct
        """)
        self._conn.unregister("_earnings_staging")
        return df.height

    def query_earnings(self, symbol: str) -> pl.DataFrame:
        return self._conn.execute(  # type: ignore[no-any-return]
            "SELECT * FROM earnings WHERE symbol = ? ORDER BY report_date", [symbol]
        ).pl()

    # ── FX rates ─────────────────────────────────────────────────────────────

    def upsert_fx(self, pair: str, df: pl.DataFrame) -> int:
        if df.is_empty():
            return 0
        # Drop NaN rates (yfinance returns NaN for the current trading day before market close)
        df = df.filter(pl.col("rate").is_not_nan() & pl.col("rate").is_not_null())
        if df.is_empty():
            return 0
        fx_df = df.with_columns(pl.lit(pair).alias("pair")).select(["pair", "date", "rate"])
        self._conn.register("_fx_staging", fx_df.to_arrow())
        self._conn.execute("""
            INSERT INTO fx_rates (pair, date, rate)
            SELECT pair, date, rate
            FROM _fx_staging
            ON CONFLICT (pair, date) DO UPDATE SET rate = excluded.rate
        """)
        self._conn.unregister("_fx_staging")
        return fx_df.height

    def query_fx(self, pair: str, start: str | None = None, end: str | None = None) -> pl.DataFrame:
        conditions = ["pair = ?"]
        params: list[Any] = [pair]
        if start:
            conditions.append("date >= ?")
            params.append(start)
        if end:
            conditions.append("date <= ?")
            params.append(end)
        where = "WHERE " + " AND ".join(conditions)
        result = self._conn.execute(
            f"SELECT date, rate FROM fx_rates {where} ORDER BY date", params
        ).arrow()
        return pl.from_arrow(result)  # type: ignore[return-value]

    # ── Universe ──────────────────────────────────────────────────────────────

    def upsert_universe(self, df: pl.DataFrame) -> int:
        if df.is_empty():
            return 0
        self._conn.register("_univ_staging", df.to_arrow())
        self._conn.execute("""
            INSERT INTO universe (symbol, market, name, sector)
            SELECT symbol, market, name, sector
            FROM _univ_staging
            ON CONFLICT (symbol) DO UPDATE SET
                market = excluded.market,
                name   = excluded.name,
                sector = excluded.sector
        """)
        self._conn.unregister("_univ_staging")
        return df.height

    def query_universe(self, market: str | None = None) -> pl.DataFrame:
        """Query universe table with optional market filter."""
        if market:
            result = self._conn.execute(
                "SELECT * FROM universe WHERE market = ? ORDER BY symbol", [market]
            ).arrow()
        else:
            result = self._conn.execute("SELECT * FROM universe ORDER BY symbol").arrow()
        return pl.from_arrow(result)  # type: ignore[return-value]

    # ── Utility ───────────────────────────────────────────────────────────────

    def get_symbol_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv").fetchone()
        return int(row[0]) if row else 0  # type: ignore[index]

    def get_date_range(self, symbol: str) -> tuple[str | None, str | None]:
        row = self._conn.execute(
            "SELECT MIN(date)::VARCHAR, MAX(date)::VARCHAR FROM ohlcv WHERE symbol = ?", [symbol]
        ).fetchone()
        if row:
            return row[0], row[1]
        return None, None

    def get_fx_last_date(self, pair: str) -> str | None:
        """Return the last stored date for an FX pair, or None if no data exists."""
        row = self._conn.execute(
            "SELECT MAX(date)::VARCHAR FROM fx_rates WHERE pair = ?", [pair]
        ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def get_min_last_date(self, symbols: list[str]) -> str | None:
        """Return the minimum of per-symbol max(date), or None if any symbol has no data.

        Used by incremental update to find the earliest last-stored date across all
        symbols, ensuring no symbol is left behind when fetching new data.
        """
        if not symbols:
            return None
        placeholders = ", ".join(["?" for _ in symbols])
        row = self._conn.execute(
            f"""
            SELECT MIN(last_date)::VARCHAR, COUNT(*) AS n
            FROM (
                SELECT symbol, MAX(date) AS last_date
                FROM ohlcv
                WHERE symbol IN ({placeholders})
                GROUP BY symbol
            )
            """,
            symbols,
        ).fetchone()
        if row is None:
            return None
        min_date, n_with_data = row[0], row[1]
        if n_with_data < len(symbols):
            return None  # some symbols have no data → full historical load needed
        return str(min_date) if min_date is not None else None
