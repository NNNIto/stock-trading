"""Walk-forward analysis CLI.

Usage
-----
    uv run python scripts/walkforward.py \\
        --is-start 2018-01-01 --is-end 2024-12-31 \\
        [--market JP|US|all] \\
        [--symbols AAPL 7203.T ...] \\
        [--capital 3000000] \\
        [--out results/wf_run.json]

Outputs
-------
* Console: per-window table + robustness summary
* results/<name>.json: summary (Git-tracked)
* results/raw/<name>_val_trades.parquet: validation trades (excluded from Git)
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

from src.backtest.execution import ExecutionConfig
from src.backtest.walkforward import WalkForwardResult, WalkForwardRunner
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
        logger.error("No OHLCV data found. Run scripts/data_update.py to populate the database.")
        sys.exit(1)

    logger.info(f"Loaded {df.height} rows for {df['symbol'].n_unique()} symbols")
    return add_indicators_batch(df)


def _print_window_table(result: WalkForwardResult) -> None:
    header = f"{'Win':>3}  {'Train':22}  {'Val':22}  {'Train Sharpe':>12}  {'Val Sharpe':>10}  Best params"
    print(header)
    print("-" * len(header))
    for w in result.windows:
        train_range = f"{w.train_start} → {w.train_end}"
        val_range = f"{w.val_start} → {w.val_end}"
        params_str = ", ".join(f"{sid}:{list(p.values())}" for sid, p in w.best_params.items() if p)
        print(
            f"{w.window_id:>3}  {train_range:22}  {val_range:22}  "
            f"{w.train_metrics.sharpe_ratio:>12.3f}  {w.val_metrics.sharpe_ratio:>10.3f}  "
            f"{params_str}"
        )
    print()
    robust = "✓ ROBUST" if result.is_robust else "✗ OVERFITTING SUSPECTED"
    print(f"Median train Sharpe : {result.median_train_sharpe:.3f}")
    print(f"Median val   Sharpe : {result.median_val_sharpe:.3f}")
    print(f"Degradation ratio   : {result.degradation_ratio:.3f}  {robust}")


def run(
    is_start: date,
    is_end: date,
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

    runner = WalkForwardRunner(
        scenarios=scenarios,
        sizer=sizer,
        exec_config=exec_cfg,
        initial_capital=capital,
        train_months=settings.backtest.learning_window_months,
        val_months=settings.backtest.validation_window_months,
        step_months=settings.backtest.walkforward_step_months,
        max_positions=settings.risk.max_positions,
        random_seed=settings.backtest.random_seed,
    )

    symbols = _resolve_symbols(symbols, market, is_start)
    data = _load_data(symbols, market, is_start.isoformat(), is_end.isoformat())
    result = runner.run(data, is_start, is_end)

    print(f"\n{'═'*70}")
    print("Walk-Forward Analysis Results")
    print(f"{'═'*70}\n")
    _print_window_table(result)

    # ── Save results ──────────────────────────────────────────────────────────
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(exist_ok=True)
    _RAW_DIR.mkdir(exist_ok=True)

    windows_data = [
        {
            "window_id": w.window_id,
            "train_start": w.train_start.isoformat(),
            "train_end": w.train_end.isoformat(),
            "val_start": w.val_start.isoformat(),
            "val_end": w.val_end.isoformat(),
            "best_params": w.best_params,
            "train_sharpe": round(w.train_metrics.sharpe_ratio, 4),
            "val_sharpe": round(w.val_metrics.sharpe_ratio, 4),
            "val_max_dd": round(w.val_metrics.max_drawdown, 4),
            "val_win_rate": round(w.val_metrics.win_rate, 4),
            "val_trade_count": w.val_metrics.trade_count,
        }
        for w in result.windows
    ]

    summary = {
        "run_id": run_id,
        "git_hash": _git_hash(),
        "is_start": is_start.isoformat(),
        "is_end": is_end.isoformat(),
        "capital_jpy": capital,
        "market": market or "all",
        "n_windows": result.n_windows,
        "degradation_ratio": round(result.degradation_ratio, 4),
        "is_robust": result.is_robust,
        "median_train_sharpe": round(result.median_train_sharpe, 4),
        "median_val_sharpe": round(result.median_val_sharpe, 4),
        "windows": windows_data,
    }

    json_path = out_path or (_RESULTS_DIR / f"walkforward_{run_id}.json")
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    logger.info(f"Summary saved → {json_path}")

    if not result.all_val_trades.is_empty():
        trades_path = _RAW_DIR / f"walkforward_{run_id}_val_trades.parquet"
        result.all_val_trades.write_parquet(trades_path)
        logger.info(f"Val trades    → {trades_path}")


def _parse_args() -> argparse.Namespace:
    cfg = get_settings()
    oos_start = cfg.backtest.out_of_sample_start
    # Default IS period: data start through day before OOS start
    from datetime import timedelta

    oos_dt = date.fromisoformat(oos_start)
    default_is_end = (oos_dt - timedelta(days=1)).isoformat()

    p = argparse.ArgumentParser(description="Walk-forward analysis")
    p.add_argument(
        "--is-start",
        default=cfg.data.start_date,
        help=f"IS start date (default: {cfg.data.start_date})",
    )
    p.add_argument(
        "--is-end",
        default=default_is_end,
        help=f"IS end date (default: {default_is_end})",
    )
    p.add_argument("--market", choices=["JP", "US", "all"], default="all")
    p.add_argument("--symbols", nargs="+")
    p.add_argument(
        "--capital",
        type=float,
        default=float(cfg.project.capital_jpy),
    )
    p.add_argument("--out", type=Path)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        is_start=date.fromisoformat(args.is_start),
        is_end=date.fromisoformat(args.is_end),
        symbols=args.symbols,
        market=None if args.market == "all" else args.market,
        capital=args.capital,
        out_path=args.out,
    )
