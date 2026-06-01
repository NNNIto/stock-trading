"""Tests for Slack notification module."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from src.notification.slack import (
    SlackNotifier,
    notify_circuit_breaker,
    notify_daily_summary,
    notify_error,
    notify_exit_signal,
    notify_new_signals,
)


def test_no_webhook_returns_false() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert notify_new_signals([{"symbol": "AAPL", "scenario_id": "S2"}]) is False


def test_empty_signals_returns_true() -> None:
    assert notify_new_signals([]) is True


def test_webhook_called_on_signal(tmp_path) -> None:
    calls = []
    with patch("urllib.request.urlopen", side_effect=lambda req, **kw: calls.append(req)):
        with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "http://fake"}):
            result = notify_new_signals([{"symbol": "AAPL", "scenario_id": "S2"}])
    assert result is True
    assert len(calls) == 1


def test_circuit_breaker_no_crash() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert notify_circuit_breaker(-0.22, 780_000) is False


def test_daily_summary_no_crash() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert notify_daily_summary(date(2024, 1, 3), 2, 1, 3, 1_050_000) is False


def test_exit_signal_no_crash() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert notify_exit_signal("AAPL", "stop_loss", 100.0, 92.0, -820.0) is False


def test_error_no_crash() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert notify_error("test error message") is False


# ── SlackNotifier class tests ─────────────────────────────────────────────────


def test_notifier_no_url_returns_false() -> None:
    with patch.dict("os.environ", {}, clear=True):
        notifier = SlackNotifier(webhook_url=None)
        assert notifier.notify_error("test") is False


def test_notifier_notify_order_executed_sends() -> None:
    calls: list = []
    with patch("urllib.request.urlopen", side_effect=lambda req, **kw: calls.append(req)):
        notifier = SlackNotifier(webhook_url="http://fake")
        result = notifier.notify_order_executed(
            {"symbol": "AAPL", "quantity": 100, "price": 178.5, "mode": "paper"}
        )
    assert result is True


def test_notifier_daily_summary_sends() -> None:
    calls: list = []
    with patch("urllib.request.urlopen", side_effect=lambda req, **kw: calls.append(req)):
        notifier = SlackNotifier(webhook_url="http://fake")
        result = notifier.notify_daily_summary(date(2024, 1, 3), 2, 1, 3, 1_050_000, 5000.0)
    assert result is True


def test_notifier_exit_signal_sends() -> None:
    calls: list = []
    with patch("urllib.request.urlopen", side_effect=lambda req, **kw: calls.append(req)):
        notifier = SlackNotifier(webhook_url="http://fake")
        result = notifier.notify_exit_signal("AAPL", "stop_loss", 100.0, 92.0, -800.0)
    assert result is True


def test_notifier_circuit_breaker_sends() -> None:
    calls: list = []
    with patch("urllib.request.urlopen", side_effect=lambda req, **kw: calls.append(req)):
        notifier = SlackNotifier(webhook_url="http://fake")
        result = notifier.notify_circuit_breaker(-0.22, 780_000.0)
    assert result is True


def test_notifier_new_signals_empty_returns_true() -> None:
    notifier = SlackNotifier(webhook_url="http://fake")
    assert notifier.notify_new_signals([]) is True


def test_notifier_network_error_returns_false() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        notifier = SlackNotifier(webhook_url="http://fake")
        assert notifier.notify_error("boom") is False
