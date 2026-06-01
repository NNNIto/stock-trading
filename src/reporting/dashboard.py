"""Streamlit dashboard for portfolio monitoring.

Launch:
    uv run streamlit run src/reporting/dashboard.py

Layout:
  Sidebar : 表示期間 (日付レンジ) + 市場フィルター
  Main    : KPI カード / 資産推移 / シグナル一覧 / 日次損益チャート
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

try:
    import streamlit as st

    _STREAMLIT = True
except ImportError:
    _STREAMLIT = False

_RESULTS_DIR = Path(__file__).parent.parent.parent / "results"


# ── Data loaders (pure functions — no st.* calls, fully testable) ─────────────


def _load_equity_curve(results_dir: Path = _RESULTS_DIR) -> pl.DataFrame:
    """Load results/equity_curve.csv; return empty DataFrame if absent."""
    path = results_dir / "equity_curve.csv"
    if not path.exists():
        return pl.DataFrame()
    try:
        return pl.read_csv(path, try_parse_dates=True)
    except Exception:
        return pl.DataFrame()


def _load_trades(
    start: date | None = None,
    market: str | None = None,
    results_dir: Path = _RESULTS_DIR,
) -> pl.DataFrame:
    """Load closed trades from results/trades.csv (written by run_backtest.py).

    Falls back to DuckDB trades table when the CSV is absent (live/paper mode).
    """
    mkt = None if market == "全て" else market

    # CSV path — populated by run_backtest.py after each backtest run
    csv_path = results_dir / "trades.csv"
    if csv_path.exists():
        try:
            df = pl.read_csv(csv_path, try_parse_dates=True)
            if start and "exit_date" in df.columns:
                df = df.filter(pl.col("exit_date") >= start)
            if mkt and "market" in df.columns:
                df = df.filter(pl.col("market") == mkt)
            return df
        except Exception:
            pass

    # Fallback: DuckDB trades table (live/paper trades entered via PortfolioManager)
    from src.data.repository import Repository

    start_str = start.isoformat() if start else None
    try:
        with Repository() as repo:
            return repo.query_trades(start=start_str, market=mkt)
    except Exception:
        return pl.DataFrame()


def _load_signals(
    limit: int = 30,
    start: date | None = None,
    market: str | None = None,
) -> pl.DataFrame:
    """Load recent signals from DuckDB; return empty DataFrame on any error."""
    from src.data.repository import Repository

    start_str = start.isoformat() if start else None
    mkt = None if market == "全て" else market
    try:
        with Repository() as repo:
            return repo.query_signals_recent(limit=limit, start=start_str, market=mkt)
    except Exception:
        return pl.DataFrame()


def _compute_kpis(
    trades: pl.DataFrame,
    equity: pl.DataFrame,
) -> dict[str, Any]:
    """Derive KPI values from trades and equity curve DataFrames."""
    kpis: dict[str, Any] = {
        "cumulative_pnl": 0.0,
        "win_rate": None,
        "max_drawdown": None,
        "sharpe": None,
        "trade_count": 0,
    }

    if not trades.is_empty() and "pnl" in trades.columns:
        pnl = trades["pnl"].drop_nulls().cast(pl.Float64)
        kpis["cumulative_pnl"] = float(pnl.sum() or 0.0)  # type: ignore[arg-type]
        kpis["trade_count"] = len(pnl)
        if len(pnl) > 0:
            wins = int((pnl > 0).sum())  # type: ignore[arg-type]
            kpis["win_rate"] = wins / len(pnl)

    if not equity.is_empty() and "portfolio_value" in equity.columns:
        vals = equity["portfolio_value"].drop_nulls().cast(pl.Float64)
        if len(vals) > 1:
            peak = vals.cum_max()
            dd_series = (vals - peak) / peak
            dd_min = dd_series.min()
            kpis["max_drawdown"] = float(dd_min) if dd_min is not None else None  # type: ignore[arg-type]

            rets = vals.pct_change().drop_nulls()
            if len(rets) > 1:
                std_val = rets.std()
                std = float(std_val) if std_val is not None else 0.0  # type: ignore[arg-type]
                if std > 0:
                    mean_val = rets.mean()
                    if mean_val is not None:
                        kpis["sharpe"] = float(mean_val) / std * (252**0.5)  # type: ignore[arg-type]

    return kpis


# ── Page renderers (Streamlit-dependent) ─────────────────────────────────────


def _render_kpi_section(kpis: dict[str, Any]) -> None:
    st.subheader("KPI")
    c1, c2, c3, c4 = st.columns(4)
    pnl = kpis["cumulative_pnl"]
    c1.metric("累計損益", f"¥{pnl:+,.0f}")
    wr = kpis["win_rate"]
    c2.metric("勝率", f"{wr:.1%}" if wr is not None else "—")
    dd = kpis["max_drawdown"]
    c3.metric("最大ドローダウン", f"{dd:.1%}" if dd is not None else "—")
    sr = kpis["sharpe"]
    c4.metric("シャープ比", f"{sr:.2f}" if sr is not None else "—")


def _render_equity_section(equity: pl.DataFrame) -> None:
    import plotly.express as px

    st.subheader("資産推移")
    if equity.is_empty():
        st.info("results/equity_curve.csv が見つかりません")
        return
    fig = px.line(
        equity.to_pandas(),
        x="date",
        y="portfolio_value",
        labels={"portfolio_value": "評価額 (JPY)", "date": "日付"},
    )
    fig.update_layout(margin={"l": 0, "r": 0, "t": 20, "b": 0})
    st.plotly_chart(fig, use_container_width=True)


def _render_signals_section(signals: pl.DataFrame) -> None:
    st.subheader("シグナル一覧（最新30件）")
    if signals.is_empty():
        st.info("シグナルデータなし")
        return

    def _color_row(row: Any) -> list[str]:
        action = str(row.get("action", ""))
        if action == "BUY":
            bg = "background-color: #d4edda"
        elif action == "SELL":
            bg = "background-color: #f8d7da"
        else:
            bg = "background-color: #e2e3e5"
        return [bg] * len(row)

    display_cols = [
        c
        for c in ("signal_date", "symbol", "scenario_id", "action", "expected_entry_price")
        if c in signals.columns
    ]
    df_pd = signals.select(display_cols).to_pandas()
    styled = df_pd.style.apply(_color_row, axis=1)
    st.dataframe(styled, use_container_width=True)


def _render_daily_pnl_section(trades: pl.DataFrame) -> None:
    import plotly.graph_objects as go

    st.subheader("日次損益")
    if trades.is_empty() or "pnl" not in trades.columns or "exit_date" not in trades.columns:
        st.info("トレードデータなし")
        return

    daily = (
        trades.group_by("exit_date").agg(pl.col("pnl").sum().alias("daily_pnl")).sort("exit_date")
    )
    df_pd = daily.to_pandas()
    colors = ["#28a745" if v >= 0 else "#dc3545" for v in df_pd["daily_pnl"]]
    fig = go.Figure(go.Bar(x=df_pd["exit_date"], y=df_pd["daily_pnl"], marker_color=colors))
    fig.update_layout(
        yaxis_title="損益 (JPY)",
        xaxis_title="日付",
        margin={"l": 0, "r": 0, "t": 20, "b": 0},
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="Stock Trading Dashboard", layout="wide")
    st.title("株式投資自動化システム — ダッシュボード")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    st.sidebar.header("フィルター")
    today = date.today()
    date_from = st.sidebar.date_input("開始日", value=today - timedelta(days=180))
    date_to = st.sidebar.date_input("終了日", value=today)
    market = st.sidebar.selectbox("市場", ["全て", "US", "JP"])

    start_date: date = date_from if isinstance(date_from, date) else today - timedelta(days=180)
    end_date: date = date_to if isinstance(date_to, date) else today

    # ── Load data ─────────────────────────────────────────────────────────────
    equity = _load_equity_curve()
    if not equity.is_empty() and "date" in equity.columns:
        equity = equity.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))

    trades = _load_trades(start=start_date, market=market)
    signals = _load_signals(limit=30, start=start_date, market=market)
    kpis = _compute_kpis(trades, equity)

    # ── Render sections ───────────────────────────────────────────────────────
    _render_kpi_section(kpis)
    st.divider()
    _render_equity_section(equity)
    st.divider()
    _render_signals_section(signals)
    st.divider()
    _render_daily_pnl_section(trades)


if __name__ == "__main__" and _STREAMLIT:
    main()
