"""Backtest engine: daily-loop simulation of scenario signals against historical data."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from src.backtest.execution import ExecutionConfig, Fill, execute_buy, execute_sell
from src.portfolio.sizer import PositionSizer
from src.scenarios.base import ExitReason, Position, ScenarioBase
from src.utils.logger import get_logger

logger = get_logger()

# Scenario priority for conflict resolution (higher index = higher priority)
_SCENARIO_PRIORITY: dict[str, int] = {"S6": 0, "S3": 1, "S2": 2, "S4": 3}


# ── Internal position tracking ────────────────────────────────────────────────


@dataclass
class _OpenPosition:
    """Mutable per-position state tracked by the engine."""

    symbol: str
    scenario_id: str
    entry_date: date
    entry_price: float
    quantity: int
    market: str
    peak_price: float
    buy_total_cost: float  # buy_fill.net_value — includes gross + commission + FX
    holding_days: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_position(self) -> Position:
        return Position(
            symbol=self.symbol,
            scenario_id=self.scenario_id,
            entry_date=self.entry_date,
            entry_price=self.entry_price,
            quantity=self.quantity,
            market=self.market,
            peak_price=self.peak_price,
            holding_days=self.holding_days,
            metadata=self.metadata,
        )


# ── Pending order queues ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _PendingEntry:
    symbol: str
    scenario_id: str
    capital_jpy: float
    market: str
    priority: int  # higher wins conflict resolution


@dataclass(frozen=True)
class _PendingSell:
    symbol: str
    quantity: int
    market: str
    exit_reason: str
    scenario_id: str
    entry_date: date
    entry_price: float
    buy_total_cost: float  # full buy outflow including fees — for accurate PnL
    entry_fill_id: str = ""


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class TradeRecord:
    """Completed round-trip trade."""

    trade_id: str
    mode: str  # 'backtest'
    scenario_id: str
    scenario_version: str
    symbol: str
    market: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    quantity: int
    fees: float  # total fees (commission + fx, both legs)
    pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str


@dataclass
class BacktestResult:
    trades: pl.DataFrame
    equity_curve: pl.DataFrame  # columns: date, portfolio_value, cash
    open_positions: list[Position]  # positions still open at backtest end


# ── Macro filter ──────────────────────────────────────────────────────────────


class MacroFilter:
    """Block new entries during high-risk macro conditions.

    Pass vix_data / nikkei_data as {date: float} dicts.  If a series is
    absent the corresponding filter is silently skipped.

    Pass jp_index_above_ma200 / us_index_above_ma200 as {date: bool} dicts
    to enable a market-specific trend filter (block entries when index < 200MA).
    """

    def __init__(
        self,
        vix_threshold: float = 35.0,
        nikkei_drawdown_threshold: float = -0.10,
        blackout_dates: set[date] | None = None,
        vix_data: dict[date, float] | None = None,
        nikkei_data: dict[date, float] | None = None,
        jp_index_above_ma200: dict[date, bool] | None = None,
        us_index_above_ma200: dict[date, bool] | None = None,
    ) -> None:
        self.vix_threshold = vix_threshold
        self.nikkei_drawdown_threshold = nikkei_drawdown_threshold
        self.blackout_dates: set[date] = blackout_dates or set()
        self.vix_data = vix_data or {}
        self.nikkei_data = nikkei_data or {}
        self.jp_index_above_ma200 = jp_index_above_ma200 or {}
        self.us_index_above_ma200 = us_index_above_ma200 or {}
        self._nikkei_prev: dict[date, float] = {}

    def is_entry_blocked(self, current_date: date) -> bool:
        if current_date in self.blackout_dates:
            return True
        vix = self.vix_data.get(current_date)
        if vix is not None and vix > self.vix_threshold:
            logger.debug(f"macro: entry blocked on {current_date} (VIX={vix:.1f})")
            return True
        # Nikkei drawdown: compare to rolling peak (approximate via daily return)
        nk = self.nikkei_data.get(current_date)
        if nk is not None:
            prev = self._nikkei_prev.get(current_date)
            if prev is not None and prev > 0:
                daily_ret = (nk - prev) / prev
                if daily_ret < self.nikkei_drawdown_threshold:
                    logger.debug(f"macro: entry blocked on {current_date} (Nikkei {daily_ret:.1%})")
                    return True
        return False

    def is_market_blocked(self, current_date: date, market: str) -> bool:
        """Return True when the market index is below its 200-day MA."""
        if market == "JP" and self.jp_index_above_ma200:
            above = self.jp_index_above_ma200.get(current_date)
            if above is not None and not above:
                logger.debug(f"macro: JP entry blocked on {current_date} (index < 200MA)")
                return True
        if market == "US" and self.us_index_above_ma200:
            above = self.us_index_above_ma200.get(current_date)
            if above is not None and not above:
                logger.debug(f"macro: US entry blocked on {current_date} (index < 200MA)")
                return True
        return False

    def update(self, current_date: date) -> None:
        nk = self.nikkei_data.get(current_date)
        if nk is not None:
            self._nikkei_prev[current_date] = nk


# ── Engine ────────────────────────────────────────────────────────────────────


class BacktestEngine:
    """Event-driven daily backtest engine.

    Contract:
    - ``data`` must contain pre-computed indicators (output of add_indicators_batch).
    - Signals generated on date T are executed at T+1 open price (翌営業日始値約定).
    - Exit signals are also executed at T+1 open.
    - same random_seed → identical results.
    """

    def __init__(
        self,
        scenarios: list[ScenarioBase],
        sizer: PositionSizer,
        exec_config: ExecutionConfig,
        initial_capital: float,
        max_positions: int = 7,
        max_sector_positions: int = 3,
        random_seed: int = 42,
        sector_map: dict[str, str] | None = None,
        macro_filter: MacroFilter | None = None,
        mode: str = "backtest",
    ) -> None:
        self.scenarios = [s for s in scenarios if s.is_enabled]
        self.sizer = sizer
        self.exec_config = exec_config
        self.initial_capital = initial_capital
        self.max_positions = max_positions
        self.max_sector_positions = max_sector_positions
        self.random_seed = random_seed
        self.sector_map = sector_map or {}
        self.macro_filter = macro_filter or MacroFilter()
        self.mode = mode

    def run(
        self,
        data: pl.DataFrame,
        start_date: date,
        end_date: date,
    ) -> BacktestResult:
        """Run the backtest and return trades + equity curve.

        Args:
            data: Full OHLCV + indicator DataFrame for all symbols.
                  Must have columns: symbol, date, open, close, market,
                  plus all indicator columns required by the active scenarios.
            start_date: First date to process signals (inclusive).
            end_date:   Last date to process signals (inclusive).
        """
        random.seed(self.random_seed)

        # ── Pre-compute buy signals ──────────────────────────────────────────
        logger.info("backtest: pre-computing signals …")
        signals_by_date = self._precompute_signals(data, start_date, end_date)

        # ── Build price/indicator lookup: {(symbol, date) → row dict} ───────
        logger.info("backtest: building price lookup …")
        price_lookup = self._build_lookup(data)

        # ── Daily simulation loop ────────────────────────────────────────────
        trading_days = sorted(
            d
            for d in data.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))[
                "date"
            ]
            .unique()
            .to_list()
        )

        cash = self.initial_capital
        open_positions: dict[str, _OpenPosition] = {}
        completed_trades: list[TradeRecord] = []
        equity_rows: list[dict[str, Any]] = []

        pending_entries: list[_PendingEntry] = []
        pending_sells: list[_PendingSell] = []

        for current_date in trading_days:
            day_prices = price_lookup.get(current_date, {})

            # ── Execute pending sells (from yesterday's exit signals) ────────
            still_pending_sells = []
            for order in pending_sells:
                row = day_prices.get(order.symbol)
                if row is None or row.get("open") is None:
                    still_pending_sells.append(order)  # retry next day
                    continue
                fill = execute_sell(
                    symbol=order.symbol,
                    trade_date=current_date,
                    open_price=float(row["open"]),
                    quantity=order.quantity,
                    market=order.market,
                    config=self.exec_config,
                )
                cash += fill.net_value
                open_positions.pop(order.symbol, None)

                # Build trade record
                # pnl = sell net proceeds - total buy cost (both legs, all fees)
                pnl = fill.net_value - order.buy_total_cost
                pnl_pct = pnl / order.buy_total_cost if order.buy_total_cost != 0 else 0.0
                buy_fees = order.buy_total_cost - order.entry_price * order.quantity
                holding_days = (current_date - order.entry_date).days

                completed_trades.append(
                    TradeRecord(
                        trade_id=str(uuid.uuid4()),
                        mode=self.mode,
                        scenario_id=order.scenario_id,
                        scenario_version="",
                        symbol=order.symbol,
                        market=order.market,
                        entry_date=order.entry_date,
                        entry_price=order.entry_price,
                        exit_date=current_date,
                        exit_price=fill.fill_price,
                        quantity=order.quantity,
                        fees=buy_fees + fill.commission + fill.fx_cost,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        holding_days=holding_days,
                        exit_reason=order.exit_reason,
                    )
                )

            pending_sells = still_pending_sells

            # ── Execute pending entries (from yesterday's entry signals) ─────
            still_pending_entries: list[_PendingEntry] = []
            for entry_order in sorted(pending_entries, key=lambda o: -o.priority):
                if len(open_positions) >= self.max_positions:
                    break  # no more room today
                if entry_order.symbol in open_positions:
                    continue  # already have a position
                entry_row = day_prices.get(entry_order.symbol)
                if entry_row is None or entry_row.get("open") is None:
                    still_pending_entries.append(entry_order)
                    continue
                portfolio_value = cash + self._mark_to_market(open_positions, day_prices)
                capital = self.sizer.capital_for_position(portfolio_value, len(open_positions))
                buy_fill: Fill | None = execute_buy(
                    symbol=entry_order.symbol,
                    trade_date=current_date,
                    open_price=float(entry_row["open"]),
                    capital_jpy=capital,
                    market=entry_order.market,
                    config=self.exec_config,
                )
                if buy_fill is None:
                    continue
                if buy_fill.net_value > cash:
                    continue  # insufficient cash
                cash -= buy_fill.net_value
                open_positions[entry_order.symbol] = _OpenPosition(
                    symbol=entry_order.symbol,
                    scenario_id=entry_order.scenario_id,
                    entry_date=current_date,
                    entry_price=buy_fill.fill_price,
                    quantity=buy_fill.quantity,
                    market=entry_order.market,
                    peak_price=buy_fill.fill_price,
                    buy_total_cost=buy_fill.net_value,
                    holding_days=0,
                )

            pending_entries = still_pending_entries

            # ── Update open positions (holding_days, peak_price) ─────────────
            # holding_days counts TRADING DAYS (days with close data).
            # TradeRecord.holding_days uses calendar days for reporting.
            # scenario time_exit_days parameters are in trading days.
            for sym, pos in list(open_positions.items()):
                row = day_prices.get(sym)
                if row and row.get("close") is not None:
                    pos.holding_days += 1
                    pos.peak_price = max(pos.peak_price, float(row["close"]))

            # ── Record equity curve ──────────────────────────────────────────
            mtm = self._mark_to_market(open_positions, day_prices)
            equity_rows.append({"date": current_date, "portfolio_value": cash + mtm, "cash": cash})

            # ── Check exit signals for open positions → queue sells ──────────
            for sym, pos in open_positions.items():
                row = day_prices.get(sym)
                if row is None:
                    continue
                scenario = self._get_scenario(pos.scenario_id)
                if scenario is None:
                    continue
                reason = scenario.get_exit_signal(pos.to_position(), row)
                if reason != ExitReason.NO_EXIT:
                    pending_sells.append(
                        _PendingSell(
                            symbol=sym,
                            quantity=pos.quantity,
                            market=pos.market,
                            exit_reason=reason,
                            scenario_id=pos.scenario_id,
                            entry_date=pos.entry_date,
                            entry_price=pos.entry_price,
                            buy_total_cost=pos.buy_total_cost,
                        )
                    )

            # ── Generate entry signals → queue buys ─────────────────────────
            if not self.macro_filter.is_entry_blocked(current_date):
                all_signals = signals_by_date.get(current_date, [])
                day_signals = [
                    s
                    for s in all_signals
                    if not self.macro_filter.is_market_blocked(current_date, s.market)
                ]
                new_entries, forced_sells = self._resolve_conflicts(
                    day_signals,
                    open_positions,
                    {o.symbol for o in pending_sells},
                    day_prices,
                    cash + self._mark_to_market(open_positions, day_prices),
                )
                pending_entries.extend(new_entries)
                pending_sells.extend(forced_sells)

            self.macro_filter.update(current_date)

        # ── Mark remaining open positions to last-day close (end_of_backtest) ─
        # Use the last trading day's prices for MTM exit; these positions are
        # included in trades so performance metrics capture unrealized PnL.
        last_day_prices = price_lookup.get(trading_days[-1], {}) if trading_days else {}
        for sym, pos in open_positions.items():
            last_row = last_day_prices.get(sym)
            if last_row and last_row.get("close") is not None:
                mtm_price = float(last_row["close"])
            else:
                mtm_price = pos.entry_price  # fallback: no MTM gain/loss
            mtm_gross = mtm_price * pos.quantity
            pnl = mtm_gross - pos.buy_total_cost
            pnl_pct = pnl / pos.buy_total_cost if pos.buy_total_cost != 0 else 0.0
            last_date = trading_days[-1] if trading_days else end_date
            holding_days = (last_date - pos.entry_date).days
            buy_fees = pos.buy_total_cost - pos.entry_price * pos.quantity
            completed_trades.append(
                TradeRecord(
                    trade_id=str(uuid.uuid4()),
                    mode=self.mode,
                    scenario_id=pos.scenario_id,
                    scenario_version="",
                    symbol=sym,
                    market=pos.market,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=last_date,
                    exit_price=mtm_price,
                    quantity=pos.quantity,
                    fees=buy_fees,  # sell-side fees omitted: no actual transaction
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    holding_days=holding_days,
                    exit_reason="end_of_backtest",
                )
            )

        # Build result DataFrames
        trades_df = _trades_to_df(completed_trades)
        equity_df = pl.DataFrame(equity_rows).sort("date")

        n_closed = len([t for t in completed_trades if t.exit_reason != "end_of_backtest"])
        n_open = len(open_positions)
        logger.info(
            f"backtest: {n_closed} closed + {n_open} MTM trades, "
            f"final equity {equity_rows[-1]['portfolio_value']:,.0f} JPY"
            if equity_rows
            else "backtest: 0 trades"
        )
        return BacktestResult(
            trades=trades_df,
            equity_curve=equity_df,
            open_positions=[pos.to_position() for pos in open_positions.values()],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _precompute_signals(
        self,
        data: pl.DataFrame,
        start_date: date,
        end_date: date,
    ) -> dict[date, list[_PendingEntry]]:
        """Run generate_signals for each scenario × symbol; index by signal date."""
        result: dict[date, list[_PendingEntry]] = {}
        symbols = data["symbol"].unique().to_list()
        for scenario in self.scenarios:
            priority = _SCENARIO_PRIORITY.get(scenario.scenario_id, 0)
            for symbol in symbols:
                sym_data = data.filter(pl.col("symbol") == symbol).sort("date")
                if sym_data.is_empty():
                    continue
                try:
                    sig_df = scenario.generate_signals(sym_data)
                except Exception as exc:
                    logger.warning(f"backtest: signal error {scenario.scenario_id}/{symbol}: {exc}")
                    continue
                buy_rows = sig_df.filter(
                    (pl.col("action") == "BUY")
                    & (pl.col("date") >= start_date)
                    & (pl.col("date") <= end_date)
                )
                market = sym_data["market"][0] if "market" in sym_data.columns else "JP"
                for row in buy_rows.iter_rows(named=True):
                    entry = _PendingEntry(
                        symbol=symbol,
                        scenario_id=scenario.scenario_id,
                        capital_jpy=0.0,  # filled at execution time
                        market=str(market),
                        priority=priority,
                    )
                    result.setdefault(row["date"], []).append(entry)
        return result

    def _build_lookup(self, data: pl.DataFrame) -> dict[date, dict[str, dict[str, Any]]]:
        """Build {date → {symbol → row dict}} for O(1) price/indicator access."""
        lookup: dict[date, dict[str, dict[str, Any]]] = {}
        for row in data.iter_rows(named=True):
            d = row["date"]
            sym = row["symbol"]
            lookup.setdefault(d, {})[sym] = row
        return lookup

    def _mark_to_market(
        self,
        positions: dict[str, _OpenPosition],
        day_prices: dict[str, dict[str, Any]],
    ) -> float:
        total = 0.0
        for sym, pos in positions.items():
            row = day_prices.get(sym)
            price = float(row["close"]) if row and row.get("close") is not None else pos.entry_price
            total += price * pos.quantity
        return total

    def _get_scenario(self, scenario_id: str) -> ScenarioBase | None:
        for s in self.scenarios:
            if s.scenario_id == scenario_id:
                return s
        return None

    def _resolve_conflicts(
        self,
        day_signals: list[_PendingEntry],
        open_positions: dict[str, _OpenPosition],
        symbols_being_sold: set[str],
        day_prices: dict[str, dict[str, Any]],
        portfolio_value: float,
    ) -> tuple[list[_PendingEntry], list[_PendingSell]]:
        """Apply scenarios.md section 6 conflict resolution rules.

        Returns (approved_entries, forced_sells).

        Rules:
        1. De-duplicate same symbol: keep highest-priority scenario.
        2. Skip symbols already in open positions (unless being exited today).
        3. Enforce max_positions limit.
           Exception: S4 can evict one S6 position per signal if at capacity.
        4. Enforce sector concentration limit (requires sector_map).
        """
        if not day_signals:
            return [], []

        # De-duplicate: per symbol, keep the highest-priority signal
        best: dict[str, _PendingEntry] = {}
        for sig in day_signals:
            existing = best.get(sig.symbol)
            if existing is None or sig.priority > existing.priority:
                best[sig.symbol] = sig

        approved: list[_PendingEntry] = []
        forced_sells: list[_PendingSell] = []

        # S6 positions eligible for eviction by S4 (scenarios.md 6.2)
        s6_evictable = [sym for sym, pos in open_positions.items() if pos.scenario_id == "S6"]

        for sig in sorted(best.values(), key=lambda s: -s.priority):
            # Skip if already holding this symbol (and not being exited)
            if sig.symbol in open_positions and sig.symbol not in symbols_being_sold:
                continue

            available_slots = self.max_positions - len(open_positions) + len(symbols_being_sold)

            if available_slots <= 0:
                # S4 exception: evict one S6 position to make room
                if sig.scenario_id == "S4" and s6_evictable:
                    evict_sym = s6_evictable.pop(0)
                    evict_pos = open_positions[evict_sym]
                    symbols_being_sold.add(evict_sym)
                    forced_sells.append(
                        _PendingSell(
                            symbol=evict_sym,
                            quantity=evict_pos.quantity,
                            market=evict_pos.market,
                            exit_reason="S4_eviction",
                            scenario_id=evict_pos.scenario_id,
                            entry_date=evict_pos.entry_date,
                            entry_price=evict_pos.entry_price,
                            buy_total_cost=evict_pos.buy_total_cost,
                        )
                    )
                    logger.info(f"backtest: S4 evicting S6 position in {evict_sym}")
                    available_slots = 1
                else:
                    continue

            # Sector concentration check
            sector = self.sector_map.get(sig.symbol)
            if sector:
                sector_count = sum(
                    1
                    for sym, pos in open_positions.items()
                    if self.sector_map.get(sym) == sector and sym not in symbols_being_sold
                )
                if sector_count >= self.max_sector_positions:
                    continue

            approved.append(sig)

        return approved, forced_sells


# ── Helpers ───────────────────────────────────────────────────────────────────


def _trades_to_df(records: list[TradeRecord]) -> pl.DataFrame:
    if not records:
        return pl.DataFrame(
            {
                "trade_id": pl.Series([], dtype=pl.Utf8),
                "mode": pl.Series([], dtype=pl.Utf8),
                "scenario_id": pl.Series([], dtype=pl.Utf8),
                "scenario_version": pl.Series([], dtype=pl.Utf8),
                "symbol": pl.Series([], dtype=pl.Utf8),
                "market": pl.Series([], dtype=pl.Utf8),
                "entry_date": pl.Series([], dtype=pl.Date),
                "entry_price": pl.Series([], dtype=pl.Float64),
                "exit_date": pl.Series([], dtype=pl.Date),
                "exit_price": pl.Series([], dtype=pl.Float64),
                "quantity": pl.Series([], dtype=pl.Int64),
                "fees": pl.Series([], dtype=pl.Float64),
                "pnl": pl.Series([], dtype=pl.Float64),
                "pnl_pct": pl.Series([], dtype=pl.Float64),
                "holding_days": pl.Series([], dtype=pl.Int64),
                "exit_reason": pl.Series([], dtype=pl.Utf8),
            }
        )
    rows = [
        {
            "trade_id": t.trade_id,
            "mode": t.mode,
            "scenario_id": t.scenario_id,
            "scenario_version": t.scenario_version,
            "symbol": t.symbol,
            "market": t.market,
            "entry_date": t.entry_date,
            "entry_price": t.entry_price,
            "exit_date": t.exit_date,
            "exit_price": t.exit_price,
            "quantity": t.quantity,
            "fees": t.fees,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "holding_days": t.holding_days,
            "exit_reason": t.exit_reason,
        }
        for t in records
    ]
    return pl.DataFrame(rows)
