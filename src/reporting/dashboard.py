"""Streamlit dashboard for portfolio monitoring.

Launch:
    uv run streamlit run src/reporting/dashboard.py

Pages:
  1. ポジション — open positions with unrealized P&L
  2. シグナル  — today's and recent signals
  3. パフォーマンス — per-scenario metrics
  4. エクイティカーブ — portfolio equity over time
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

# Guard: only import streamlit at runtime so unit tests can import this module
try:
    import streamlit as st

    _STREAMLIT = True
except ImportError:
    _STREAMLIT = False


# ── Data helpers ──────────────────────────────────────────────────────────────


def _load_positions() -> pl.DataFrame:
    from src.portfolio.manager import PortfolioManager

    try:
        with PortfolioManager() as pm:
            return pm.get_open_positions()
    except Exception:
        return pl.DataFrame()


def _load_signals(days: int = 7) -> pl.DataFrame:
    from src.data.repository import Repository

    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        with Repository() as repo:
            try:
                result = repo._conn.execute(
                    "SELECT * FROM signals WHERE signal_date >= ? ORDER BY signal_date DESC",
                    [start],
                ).fetchall()
                if not result:
                    return pl.DataFrame()
                cols = [d[0] for d in repo._conn.description or []]
                return pl.DataFrame([dict(zip(cols, r, strict=False)) for r in result])
            except Exception:
                return pl.DataFrame()
    except Exception:
        return pl.DataFrame()


def _load_trades(days: int = 90) -> pl.DataFrame:
    from src.data.repository import Repository

    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        with Repository() as repo:
            try:
                result = repo._conn.execute(
                    "SELECT * FROM trades WHERE exit_date >= ? ORDER BY exit_date DESC",
                    [start],
                ).fetchall()
                if not result:
                    return pl.DataFrame()
                cols = [d[0] for d in repo._conn.description or []]
                return pl.DataFrame([dict(zip(cols, r, strict=False)) for r in result])
            except Exception:
                return pl.DataFrame()
    except Exception:
        return pl.DataFrame()


# ── Page renderers ────────────────────────────────────────────────────────────


def _page_positions() -> None:
    st.header("現在ポジション")
    df = _load_positions()
    if df.is_empty():
        st.info("オープンポジションなし")
        return
    # Format for display
    display = df.select(
        [
            pl.col("symbol"),
            pl.col("scenario_id").alias("シナリオ"),
            pl.col("entry_date").alias("エントリー日"),
            pl.col("entry_price").alias("エントリー価格"),
            pl.col("quantity").alias("株数"),
            pl.col("current_price").alias("現在価格"),
            pl.col("unrealized_pnl").alias("含み損益(円)"),
            pl.col("mode"),
        ]
    )
    st.dataframe(display.to_pandas(), use_container_width=True)

    total_upnl = float(df["unrealized_pnl"].sum()) if "unrealized_pnl" in df.columns else 0.0
    col1, col2 = st.columns(2)
    col1.metric("保有銘柄数", df.height)
    col2.metric("含み損益合計", f"¥{total_upnl:+,.0f}")


def _page_signals() -> None:
    st.header("最近のシグナル（直近7日）")
    df = _load_signals(days=7)
    if df.is_empty():
        st.info("最近のシグナルなし")
        return
    st.dataframe(df.to_pandas(), use_container_width=True)


def _page_performance() -> None:
    from src.backtest.metrics import compute_metrics, format_metrics

    st.header("パフォーマンス（直近90日クローズドトレード）")
    trades = _load_trades(days=90)
    if trades.is_empty():
        st.info("過去90日に完了したトレードなし")
        return

    # Overall metrics
    dummy_eq = pl.DataFrame(
        {
            "date": [date.today()],
            "portfolio_value": [1.0],
            "cash": [1.0],
        }
    )
    m = compute_metrics(trades, dummy_eq, bootstrap_samples=0)
    st.code(format_metrics(m))

    # Per-scenario breakdown
    if "scenario_id" in trades.columns:
        st.subheader("シナリオ別")
        for scen in trades["scenario_id"].unique().to_list():
            sub = trades.filter(pl.col("scenario_id") == scen)
            st.write(f"**{scen}** ({sub.height} trades)")
            ms = compute_metrics(sub, dummy_eq, bootstrap_samples=0)
            cols = st.columns(4)
            cols[0].metric("勝率", f"{ms.win_rate:.0%}")
            cols[1].metric(
                "PF", f"{ms.profit_factor:.2f}" if ms.profit_factor != float("inf") else "∞"
            )
            cols[2].metric("Sharpe", f"{ms.sharpe_ratio:.2f}")
            cols[3].metric("取引数", ms.trade_count)


def _page_equity() -> None:
    import plotly.express as px

    from src.data.repository import Repository

    st.header("エクイティカーブ")
    try:
        with Repository() as repo:
            try:
                rows = repo._conn.execute("SELECT * FROM equity_curve ORDER BY date").fetchall()
                if not rows:
                    st.info("エクイティデータなし")
                    return
                cols = [d[0] for d in repo._conn.description or []]
                eq = pl.DataFrame([dict(zip(cols, r, strict=False)) for r in rows])
            except Exception:
                st.info("equity_curve テーブルなし")
                return
    except Exception:
        st.info("データベース接続エラー")
        return

    fig = px.line(eq.to_pandas(), x="date", y="portfolio_value", title="ポートフォリオ評価額推移")
    st.plotly_chart(fig, use_container_width=True)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="Stock Trading Dashboard", layout="wide")
    st.title("株式投資自動化システム — ダッシュボード")

    page = st.sidebar.radio(
        "ページ",
        ["ポジション", "シグナル", "パフォーマンス", "エクイティカーブ"],
    )

    if page == "ポジション":
        _page_positions()
    elif page == "シグナル":
        _page_signals()
    elif page == "パフォーマンス":
        _page_performance()
    else:
        _page_equity()


if __name__ == "__main__" and _STREAMLIT:
    main()
