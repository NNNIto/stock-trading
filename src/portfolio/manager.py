"""Portfolio manager: live position state with DuckDB persistence.

Manages the `positions` table for paper/live trading modes.
Backtest positions are handled entirely in-memory by BacktestEngine.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from src.utils.logger import get_logger

logger = get_logger()

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "trading.duckdb"


class PortfolioManager:
    """CRUD interface for the live `positions` table in DuckDB.

    The table stores one row per open position.  When a position is closed
    the row is deleted and a completed trade is written to `trades`.

    Usage::
        with PortfolioManager() as pm:
            pm.open_position(symbol="AAPL", scenario_id="S2", ...)
            positions = pm.get_open_positions()
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DB_PATH

    def __enter__(self) -> PortfolioManager:
        import duckdb

        self._conn = duckdb.connect(str(self._db_path))
        self._ensure_schema()
        return self

    def __exit__(self, *_: Any) -> None:
        self._conn.close()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol          VARCHAR PRIMARY KEY,
                scenario_id     VARCHAR NOT NULL,
                market          VARCHAR NOT NULL,
                entry_date      DATE NOT NULL,
                entry_price     DOUBLE NOT NULL,
                quantity        INTEGER NOT NULL,
                current_price   DOUBLE,
                unrealized_pnl  DOUBLE,
                stop_loss       DOUBLE,
                take_profit     DOUBLE,
                mode            VARCHAR NOT NULL DEFAULT 'paper',
                updated_at      TIMESTAMP NOT NULL
            )
        """)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_open_positions(self, mode: str | None = None) -> pl.DataFrame:
        """Return all open positions, optionally filtered by mode."""
        where = f"WHERE mode = '{mode}'" if mode else ""
        rows = self._conn.execute(f"SELECT * FROM positions {where} ORDER BY entry_date").fetchall()
        if not rows:
            return pl.DataFrame()
        cols = [d[0] for d in self._conn.description or []]
        return pl.DataFrame([dict(zip(cols, r, strict=False)) for r in rows])

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Return a single position dict or None if not open."""
        result = self._conn.execute("SELECT * FROM positions WHERE symbol = ?", [symbol]).fetchone()
        if result is None:
            return None
        cols = [
            d[0] for d in self._conn.execute("SELECT * FROM positions LIMIT 0").description or []
        ]
        return dict(zip(cols, result, strict=False))

    # ── Write ─────────────────────────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        scenario_id: str,
        market: str,
        entry_date: date,
        entry_price: float,
        quantity: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        mode: str = "paper",
    ) -> None:
        """Record a newly opened position."""
        self._conn.execute(
            """
            INSERT INTO positions
              (symbol, scenario_id, market, entry_date, entry_price, quantity,
               current_price, unrealized_pnl, stop_loss, take_profit, mode, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (symbol) DO UPDATE SET
              scenario_id   = excluded.scenario_id,
              market        = excluded.market,
              entry_date    = excluded.entry_date,
              entry_price   = excluded.entry_price,
              quantity      = excluded.quantity,
              current_price = excluded.current_price,
              stop_loss     = excluded.stop_loss,
              take_profit   = excluded.take_profit,
              mode          = excluded.mode,
              updated_at    = excluded.updated_at
            """,
            [
                symbol,
                scenario_id,
                market,
                entry_date,
                entry_price,
                quantity,
                entry_price,
                0.0,
                stop_loss,
                take_profit,
                mode,
                datetime.now(),
            ],
        )
        logger.info(f"position opened: {symbol} qty={quantity} @ {entry_price:.2f} [{mode}]")

    def update_price(self, symbol: str, current_price: float) -> None:
        """Update mark-to-market price and unrealized PnL."""
        pos = self.get_position(symbol)
        if pos is None:
            return
        upnl = (current_price - pos["entry_price"]) * pos["quantity"]
        self._conn.execute(
            "UPDATE positions SET current_price=?, unrealized_pnl=?, updated_at=? WHERE symbol=?",
            [current_price, upnl, datetime.now(), symbol],
        )

    def close_position(self, symbol: str) -> None:
        """Remove position row (caller is responsible for writing to trades table)."""
        self._conn.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
        logger.info(f"position closed: {symbol}")

    def update_prices_bulk(self, prices: dict[str, float]) -> None:
        """Batch update current prices for all tracked symbols."""
        for sym, price in prices.items():
            self.update_price(sym, price)

    # ── Summary ───────────────────────────────────────────────────────────────

    def portfolio_summary(self) -> dict[str, Any]:
        """Return aggregate stats for all open positions."""
        df = self.get_open_positions()
        if df.is_empty():
            return {"n_positions": 0, "total_cost": 0.0, "unrealized_pnl": 0.0}
        total_cost = float((df["entry_price"] * df["quantity"]).sum())
        upnl = float(df["unrealized_pnl"].sum()) if "unrealized_pnl" in df.columns else 0.0
        return {
            "n_positions": df.height,
            "total_cost": total_cost,
            "unrealized_pnl": upnl,
            "symbols": df["symbol"].to_list(),
        }
