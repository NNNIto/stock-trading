"""Backtest CLI: run a single-period backtest and save results.

Usage
-----
    uv run python scripts/run_backtest.py \\
        --start 2019-01-01 --end 2024-12-31 \\
        [--market JP|US|all] \\
        [--symbols AAPL 7203.T ...] \\
        [--capital 3000000] \\
        [--out results/my_run.json]

Outputs
-------
* Console: formatted performance metrics
* results/<name>.json: summary (Git-tracked)
* results/raw/<name>_trades.parquet: trade log (excluded from Git)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl

from src.backtest.engine import BacktestEngine, MacroFilter
from src.backtest.execution import ExecutionConfig
from src.backtest.metrics import compute_metrics, format_metrics
from src.data.indicators import add_indicators_batch
from src.data.repository import Repository
from src.data.universe import get_liquid_symbols
from src.portfolio.sizer import build_sizer
from src.scenarios.s2_breakout import S2Breakout
from src.scenarios.s3_pullback import S3Pullback
from src.scenarios.s4_pead import S4PEAD
from src.scenarios.s6_reversion import S6Reversion
from src.utils.config import get_settings
from src.utils.logger import get_logger, setup_logger

setup_logger()
logger = get_logger()

_RESULTS_DIR = Path(__file__).parent.parent / "results"
_RAW_DIR = _RESULTS_DIR / "raw"


def _git_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _resolve_symbols(
    symbols: list[str] | None,
    market: str | None,
    start: date,
) -> list[str] | None:
    """Apply liquidity filter when no explicit symbols are given."""
    settings = get_settings()
    uf = settings.universe_filter
    if symbols or not uf.enabled:
        return symbols

    markets = ["JP", "US"] if market is None else [market.upper()]
    result: list[str] = []
    with Repository() as repo:
        for mkt in markets:
            n_top = uf.jp_top_n if mkt == "JP" else uf.us_top_n
            selected = get_liquid_symbols(repo, mkt, n_top, start, uf.lookback_years)
            result.extend(selected)
            logger.info(f"universe filter: {mkt} → {len(selected)} liquid symbols (top {n_top})")
    return result or None


def _load_data(
    symbols: list[str] | None,
    market: str | None,
    start: str,
    end: str,
) -> pl.DataFrame:
    with Repository() as repo:
        df = repo.query_ohlcv(symbols=symbols, market=market, start=start, end=end)
        if df.is_empty():
            logger.error(
                "No OHLCV data found for the given filters. "
                "Run scripts/data_update.py to populate the database."
            )
            sys.exit(1)
        syms = df["symbol"].unique().to_list()
        earnings = repo.query_earnings_batch(symbols=syms, start=start, end=end)

    logger.info(f"Loaded {df.height} rows for {df['symbol'].n_unique()} symbols")
    data = add_indicators_batch(df)
    if not earnings.is_empty():
        data = data.join(earnings, on=["symbol", "date"], how="left").with_columns(
            pl.col("is_earnings_day").fill_null(False)
        )
        logger.info(f"Merged {earnings.height} earnings rows for S4 signals")
    else:
        data = data.with_columns(pl.lit(False).alias("is_earnings_day"))
    return data


def _build_index_ma_filter(start: date, end: date) -> MacroFilter:
    """Fetch market index data and compute 200MA above/below flag per date."""
    import yfinance as yf

    index_map = {"JP": "^N225", "US": "SPY"}
    result: dict[str, dict[date, bool]] = {"JP": {}, "US": {}}

    # Fetch 200 extra days before start to warm up the MA
    fetch_start = date(start.year - 1, start.month, start.day)

    for market, ticker in index_map.items():
        try:
            raw = yf.download(
                ticker,
                start=fetch_start.isoformat(),
                end=end.isoformat(),
                progress=False,
                auto_adjust=True,
            )
            if raw.empty:
                logger.warning(f"index MA filter: no data for {ticker}, skipping")
                continue
            close = raw["Close"].squeeze()
            ma200 = close.rolling(200).mean()
            above = (close > ma200).dropna()
            result[market] = {
                d.date(): bool(v) for d, v in above.items() if start <= d.date() <= end
            }
            n_blocked = sum(1 for v in result[market].values() if not v)
            logger.info(
                f"index MA filter: {ticker} — {n_blocked}/{len(result[market])} days blocked"
            )
        except Exception as e:
            logger.warning(f"index MA filter: failed to fetch {ticker}: {e}")

    return MacroFilter(
        jp_index_above_ma200=result["JP"],
        us_index_above_ma200=result["US"],
    )


def run(
    start: date,
    end: date,
    symbols: list[str] | None,
    market: str | None,
    capital: float,
    out_path: Path | None,
) -> None:
    settings = get_settings()
    exec_cfg = ExecutionConfig(
        slippage_pct=settings.execution.slippage_pct,
        commission_pct=settings.execution.commission_pct,
        fx_cost_pct=settings.execution.fx_cost_pct,
    )

    scenarios = [S2Breakout(), S3Pullback(), S4PEAD(), S6Reversion()]
    sizer = build_sizer(settings)
    macro_filter = _build_index_ma_filter(start, end)

    engine = BacktestEngine(
        scenarios=scenarios,
        sizer=sizer,
        exec_config=exec_cfg,
        initial_capital=capital,
        max_positions=settings.risk.max_positions,
        random_seed=settings.backtest.random_seed,
        macro_filter=macro_filter,
    )

    symbols = _resolve_symbols(symbols, market, start)
    data = _load_data(symbols, market, start.isoformat(), end.isoformat())
    result = engine.run(data, start, end)
    metrics = compute_metrics(
        result.trades,
        result.equity_curve,
        risk_free_rate=0.0,
        random_seed=settings.backtest.random_seed,
    )

    print(format_metrics(metrics))

    # ── Save results ──────────────────────────────────────────────────────────
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(exist_ok=True)
    _RAW_DIR.mkdir(exist_ok=True)

    summary = {
        "run_id": run_id,
        "git_hash": _git_hash(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "capital_jpy": capital,
        "market": market or "all",
        "n_symbols": data["symbol"].n_unique(),
        "trade_count": metrics.trade_count,
        "metrics": {
            "total_return_pct": round(metrics.total_return_pct, 6),
            "cagr": round(metrics.cagr, 6),
            "sharpe_ratio": round(metrics.sharpe_ratio, 4),
            "sharpe_ci_low": round(metrics.sharpe_ci_low, 4),
            "sharpe_ci_high": round(metrics.sharpe_ci_high, 4),
            "sortino_ratio": round(metrics.sortino_ratio, 4)
            if metrics.sortino_ratio != float("inf")
            else None,
            "max_drawdown": round(metrics.max_drawdown, 6),
            "avg_drawdown": round(metrics.avg_drawdown, 6),
            "win_rate": round(metrics.win_rate, 4),
            "payoff_ratio": round(metrics.payoff_ratio, 4)
            if metrics.payoff_ratio != float("inf")
            else None,
            "profit_factor": round(metrics.profit_factor, 4)
            if metrics.profit_factor != float("inf")
            else None,
            "avg_holding_days": round(metrics.avg_holding_days, 1),
            "is_reliable": metrics.is_reliable,
        },
    }

    json_path = out_path or (_RESULTS_DIR / f"backtest_{run_id}.json")
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    logger.info(f"Summary saved → {json_path}")

    # Raw trades (excluded from Git)
    if not result.trades.is_empty():
        trades_path = _RAW_DIR / f"backtest_{run_id}_trades.parquet"
        result.trades.write_parquet(trades_path)
        logger.info(f"Trades saved  → {trades_path}")

    # Dashboard CSV exports — overwrite "latest" files read by dashboard.py
    equity_csv = _RESULTS_DIR / "equity_curve.csv"
    result.equity_curve.write_csv(equity_csv)
    logger.info(f"Equity curve  → {equity_csv}")

    if not result.trades.is_empty():
        trades_csv = _RESULTS_DIR / "trades.csv"
        result.trades.write_csv(trades_csv)
        logger.info(f"Trades CSV    → {trades_csv}")


def _parse_args() -> argparse.Namespace:
    cfg = get_settings()
    p = argparse.ArgumentParser(description="Run a single-period backtest")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--market", choices=["JP", "US", "all"], default="all")
    p.add_argument("--symbols", nargs="+", help="Limit to these ticker symbols")
    p.add_argument(
        "--capital",
        type=float,
        default=float(cfg.project.capital_jpy),
        help="Initial capital in JPY",
    )
    p.add_argument("--out", type=Path, help="Path for JSON summary output")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        symbols=args.symbols,
        market=None if args.market == "all" else args.market,
        capital=args.capital,
        out_path=args.out,
    )
