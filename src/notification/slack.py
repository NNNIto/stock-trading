"""Slack notifications via Incoming Webhooks.

Set SLACK_WEBHOOK_URL environment variable to enable.
All functions are best-effort: errors are logged but not re-raised so that
notification failures never block the main trading pipeline.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from typing import Any

from src.utils.logger import get_logger

logger = get_logger()


def _send(payload: dict[str, Any], webhook_url: str | None = None) -> bool:
    """POST payload to Slack webhook; return True on success."""
    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        logger.debug("slack: SLACK_WEBHOOK_URL not set — notification skipped")
        return False
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as exc:
        logger.warning(f"slack: notification failed: {exc}")
        return False


# ── Public notification functions ─────────────────────────────────────────────


def notify_new_signals(
    signals: list[dict[str, Any]],
    signal_date: date | None = None,
) -> bool:
    """Send new BUY signal list.

    Args:
        signals: List of dicts with keys: symbol, scenario_id, expected_entry.
    """
    if not signals:
        return True
    date_str = signal_date.isoformat() if signal_date else "today"
    lines = [f"*新規シグナル ({date_str})*"]
    for s in signals:
        sym = s.get("symbol", "?")
        scen = s.get("scenario_id", "?")
        price = s.get("expected_entry_price")
        price_str = f"  想定エントリー: ¥{price:,.0f}" if price else ""
        lines.append(f"  • `{sym}` [{scen}]{price_str}")
    return _send({"text": "\n".join(lines)})


def notify_exit_signal(
    symbol: str,
    exit_reason: str,
    entry_price: float,
    current_price: float,
    pnl: float | None = None,
) -> bool:
    """Send exit recommendation for an open position."""
    pnl_str = f"  損益: {pnl:+,.0f}円" if pnl is not None else ""
    pct = (current_price - entry_price) / entry_price * 100 if entry_price else 0
    text = (
        f"*エグジット推奨*\n"
        f"  `{symbol}` — {exit_reason}\n"
        f"  エントリー: {entry_price:,.1f} → 現在: {current_price:,.1f} ({pct:+.1f}%){pnl_str}"
    )
    return _send({"text": text})


def notify_circuit_breaker(drawdown: float, portfolio_value: float) -> bool:
    """Send circuit breaker alert when portfolio DD exceeds threshold."""
    text = (
        f":rotating_light: *サーキットブレーカー発動*\n"
        f"  ポートフォリオDD: {drawdown:.1%}\n"
        f"  現在価値: ¥{portfolio_value:,.0f}\n"
        f"  → 新規エントリーを停止します"
    )
    return _send({"text": text})


def notify_daily_summary(
    signal_date: date,
    new_signals: int,
    exit_signals: int,
    open_positions: int,
    portfolio_value: float,
    daily_pnl: float | None = None,
) -> bool:
    """Send end-of-day portfolio summary."""
    pnl_str = f"\n  当日損益: {daily_pnl:+,.0f}円" if daily_pnl is not None else ""
    text = (
        f"*日次サマリー ({signal_date})*\n"
        f"  新規シグナル: {new_signals}件\n"
        f"  エグジット推奨: {exit_signals}件\n"
        f"  保有ポジション: {open_positions}件\n"
        f"  ポートフォリオ評価額: ¥{portfolio_value:,.0f}{pnl_str}"
    )
    return _send({"text": text})


def notify_error(message: str) -> bool:
    """Send error/warning alert."""
    return _send({"text": f":warning: *エラー通知*\n{message}"})
