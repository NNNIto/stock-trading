"""Business day calendar utilities for Japan and US markets."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def _get_jp_holidays(year_start: int, year_end: int) -> set[date]:
    """Return approximate Japanese market holidays (fixed + observed rules)."""
    try:
        import exchange_calendars as ecals  # type: ignore[import-untyped]

        cal = ecals.get_calendar("XTKS")
        start = pd.Timestamp(f"{year_start}-01-01")
        end = pd.Timestamp(f"{year_end}-12-31")
        sessions = cal.sessions_in_range(start, end)
        all_dates = pd.date_range(start, end)
        holidays = set(all_dates.difference(sessions).date)
        return holidays
    except Exception:
        return set()


def _get_us_holidays(year_start: int, year_end: int) -> set[date]:
    """Return US market holidays via exchange_calendars."""
    try:
        import exchange_calendars as ecals  # type: ignore[import-untyped]

        cal = ecals.get_calendar("XNYS")
        start = pd.Timestamp(f"{year_start}-01-01")
        end = pd.Timestamp(f"{year_end}-12-31")
        sessions = cal.sessions_in_range(start, end)
        all_dates = pd.date_range(start, end)
        holidays = set(all_dates.difference(sessions).date)
        return holidays
    except Exception:
        return set()


def is_jp_business_day(d: date) -> bool:
    """Return True if d is a Japan market trading day."""
    if d.weekday() >= 5:
        return False
    holidays = _get_jp_holidays(d.year, d.year)
    return d not in holidays


def is_us_business_day(d: date) -> bool:
    """Return True if d is a US market trading day."""
    if d.weekday() >= 5:
        return False
    holidays = _get_us_holidays(d.year, d.year)
    return d not in holidays


def next_jp_business_day(d: date) -> date:
    """Return the next Japan market trading day after d."""
    candidate = d + timedelta(days=1)
    while not is_jp_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def next_us_business_day(d: date) -> date:
    """Return the next US market trading day after d."""
    candidate = d + timedelta(days=1)
    while not is_us_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def business_days_between(start: date, end: date, market: str = "US") -> int:
    """Count business days between start (exclusive) and end (inclusive)."""
    checker = is_us_business_day if market == "US" else is_jp_business_day
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if checker(current):
            count += 1
        current += timedelta(days=1)
    return count
