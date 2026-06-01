"""Slack notifications via Incoming Webhooks.

Set SLACK_WEBHOOK_URL environment variable to enable.
All methods are best-effort: failures are logged but never re-raised so that
notification failures never block the main trading pipeline.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from typing import Any

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.logger import get_logger

load_dotenv()  # loads .env into os.environ once at import time
logger = get_logger()


class _SlackSettings(BaseSettings):
    slack_webhook_url: str | None = None
    # env_file intentionally omitted: load_dotenv() above already handles .env,
    # so pydantic-settings reads only os.environ — lets tests patch it cleanly.
    model_config = SettingsConfigDict(extra="ignore")


class SlackNotifier:
    """Class-based Slack notifier with Block Kit support.

    Reads SLACK_WEBHOOK_URL from env / .env via pydantic-settings.
    Pass webhook_url explicitly to override (useful in tests).
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        if webhook_url is None:
            webhook_url = _SlackSettings().slack_webhook_url
        self._url = webhook_url

    # ── Internal sender ───────────────────────────────────────────────────────

    def _send(self, payload: dict[str, Any]) -> bool:
        if not self._url:
            logger.debug("slack: SLACK_WEBHOOK_URL not set — notification skipped")
            return False
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(self._url, data, {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception as exc:
            logger.error(f"slack: notification failed: {exc}")
            return False

    # ── Public notification methods ───────────────────────────────────────────

    def notify_order_executed(self, order: dict[str, Any]) -> bool:
        """Notify order execution result (Block Kit)."""
        sym = order.get("symbol", "?")
        qty = order.get("quantity", 0)
        price = float(order.get("price") or 0)
        total = qty * price
        mode = order.get("mode", "paper")
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "注文記録"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*銘柄*\n`{sym}`"},
                    {"type": "mrkdwn", "text": f"*株数*\n{qty:,}株"},
                    {"type": "mrkdwn", "text": f"*単価*\n¥{price:,.2f}"},
                    {"type": "mrkdwn", "text": f"*投入額*\n¥{total:,.0f}"},
                    {"type": "mrkdwn", "text": f"*モード*\n{mode}"},
                ],
            },
        ]
        return self._send({"blocks": blocks})

    def notify_daily_summary(
        self,
        signal_date: date,
        new_signals: int,
        exit_signals: int,
        open_positions: int,
        portfolio_value: float,
        daily_pnl: float | None = None,
    ) -> bool:
        """Notify end-of-day portfolio summary (Block Kit)."""
        pnl_text = f"{daily_pnl:+,.0f}円" if daily_pnl is not None else "—"
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"日次サマリー ({signal_date})"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*新規シグナル*\n{new_signals}件"},
                    {"type": "mrkdwn", "text": f"*エグジット推奨*\n{exit_signals}件"},
                    {"type": "mrkdwn", "text": f"*保有ポジション*\n{open_positions}件"},
                    {"type": "mrkdwn", "text": f"*評価額*\n¥{portfolio_value:,.0f}"},
                    {"type": "mrkdwn", "text": f"*当日損益*\n{pnl_text}"},
                ],
            },
        ]
        return self._send({"blocks": blocks})

    def notify_exit_signal(
        self,
        symbol: str,
        exit_reason: str,
        entry_price: float,
        current_price: float,
        pnl: float | None = None,
    ) -> bool:
        """Notify exit recommendation (Block Kit)."""
        pct = (current_price - entry_price) / entry_price * 100 if entry_price else 0
        pnl_text = f"{pnl:+,.0f}円" if pnl is not None else "—"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "エグジット推奨"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*銘柄*\n`{symbol}`"},
                    {"type": "mrkdwn", "text": f"*理由*\n{exit_reason}"},
                    {"type": "mrkdwn", "text": f"*エントリー*\n¥{entry_price:,.1f}"},
                    {"type": "mrkdwn", "text": f"*現在値*\n¥{current_price:,.1f} ({pct:+.1f}%)"},
                    {"type": "mrkdwn", "text": f"*損益*\n{pnl_text}"},
                ],
            },
        ]
        return self._send({"blocks": blocks})

    def notify_circuit_breaker(self, drawdown: float, portfolio_value: float) -> bool:
        """Notify circuit breaker activation (Block Kit)."""
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "サーキットブレーカー発動"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*ポートフォリオDD*\n{drawdown:.1%}"},
                    {"type": "mrkdwn", "text": f"*現在価値*\n¥{portfolio_value:,.0f}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "→ 新規エントリーを停止します"},
            },
        ]
        return self._send({"blocks": blocks})

    def notify_error(self, message: str) -> bool:
        """Notify error/warning alert (Block Kit)."""
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "エラー通知"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        ]
        return self._send({"blocks": blocks})

    def notify_new_signals(
        self,
        signals: list[dict[str, Any]],
        signal_date: date | None = None,
    ) -> bool:
        """Notify list of new BUY signals (Block Kit)."""
        if not signals:
            return True
        date_str = signal_date.isoformat() if signal_date else "today"
        lines = [f"• `{s.get('symbol', '?')}` [{s.get('scenario_id', '?')}]" for s in signals]
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"新規シグナル ({date_str})"},
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ]
        return self._send({"blocks": blocks})


# ── Module-level backward-compat wrappers ─────────────────────────────────────
# daily_signals.py and other callers continue to work without changes.


def _default() -> SlackNotifier:
    return SlackNotifier()


def notify_new_signals(
    signals: list[dict[str, Any]],
    signal_date: date | None = None,
) -> bool:
    return _default().notify_new_signals(signals, signal_date)


def notify_exit_signal(
    symbol: str,
    exit_reason: str,
    entry_price: float,
    current_price: float,
    pnl: float | None = None,
) -> bool:
    return _default().notify_exit_signal(symbol, exit_reason, entry_price, current_price, pnl)


def notify_circuit_breaker(drawdown: float, portfolio_value: float) -> bool:
    return _default().notify_circuit_breaker(drawdown, portfolio_value)


def notify_daily_summary(
    signal_date: date,
    new_signals: int,
    exit_signals: int,
    open_positions: int,
    portfolio_value: float,
    daily_pnl: float | None = None,
) -> bool:
    return _default().notify_daily_summary(
        signal_date, new_signals, exit_signals, open_positions, portfolio_value, daily_pnl
    )


def notify_error(message: str) -> bool:
    return _default().notify_error(message)
