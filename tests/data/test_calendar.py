"""Tests for calendar.py"""

from datetime import date

from src.utils.calendar import (
    business_days_between,
    is_jp_business_day,
    is_us_business_day,
    next_jp_business_day,
    next_us_business_day,
)

# --- is_jp_business_day ---


def test_jp_weekday_is_business_day():
    # 2024-01-09 is Tuesday
    assert is_jp_business_day(date(2024, 1, 9)) is True


def test_jp_saturday_is_not_business_day():
    # 2024-01-06 is Saturday
    assert is_jp_business_day(date(2024, 1, 6)) is False


def test_jp_sunday_is_not_business_day():
    # 2024-01-07 is Sunday
    assert is_jp_business_day(date(2024, 1, 7)) is False


def test_jp_new_year_holiday():
    # 2024-01-01 is New Year's Day (JP market closed)
    assert is_jp_business_day(date(2024, 1, 1)) is False


# --- is_us_business_day ---


def test_us_weekday_is_business_day():
    # 2024-01-09 is Tuesday
    assert is_us_business_day(date(2024, 1, 9)) is True


def test_us_saturday_is_not_business_day():
    assert is_us_business_day(date(2024, 1, 6)) is False


def test_us_sunday_is_not_business_day():
    assert is_us_business_day(date(2024, 1, 7)) is False


def test_us_new_year_holiday():
    # 2024-01-01 is New Year's Day (US market closed)
    assert is_us_business_day(date(2024, 1, 1)) is False


def test_us_independence_day_2024():
    # 2024-07-04 is Thursday, Independence Day (US market closed)
    assert is_us_business_day(date(2024, 7, 4)) is False


# --- next_jp_business_day ---


def test_next_jp_business_day_from_friday():
    # 2024-01-05 is Friday → next business day is 2024-01-09 (Mon, not holiday)
    result = next_jp_business_day(date(2024, 1, 5))
    assert result.weekday() < 5
    assert result > date(2024, 1, 5)


def test_next_jp_business_day_skips_weekend():
    # From Friday, next should skip Sat/Sun
    friday = date(2024, 3, 1)  # Friday
    result = next_jp_business_day(friday)
    assert result >= date(2024, 3, 4)  # At least Monday


def test_next_jp_business_day_from_weekday():
    # 2024-01-09 Tuesday → 2024-01-10 Wednesday
    result = next_jp_business_day(date(2024, 1, 9))
    assert result == date(2024, 1, 10)


# --- next_us_business_day ---


def test_next_us_business_day_skips_weekend():
    friday = date(2024, 3, 1)  # Friday
    result = next_us_business_day(friday)
    assert result >= date(2024, 3, 4)


def test_next_us_business_day_from_weekday():
    # 2024-01-09 Tuesday → 2024-01-10 Wednesday
    result = next_us_business_day(date(2024, 1, 9))
    assert result == date(2024, 1, 10)


def test_next_us_business_day_skips_holiday():
    # 2024-01-01 New Year → next US business day should be 2024-01-02 (Tue)
    result = next_us_business_day(date(2023, 12, 31))
    assert result == date(2024, 1, 2)


# --- business_days_between ---


def test_business_days_between_same_week_us():
    # Mon to Fri = 4 business days (Tue, Wed, Thu, Fri)
    start = date(2024, 1, 8)  # Monday
    end = date(2024, 1, 12)  # Friday
    assert business_days_between(start, end, market="US") == 4


def test_business_days_between_includes_end():
    # start=Mon, end=Wed → Tue, Wed = 2 days
    start = date(2024, 1, 8)
    end = date(2024, 1, 10)
    assert business_days_between(start, end, market="US") == 2


def test_business_days_between_across_weekend():
    # Fri to next Mon = 1 business day (Mon only)
    start = date(2024, 1, 5)  # Friday
    end = date(2024, 1, 8)  # Monday
    result = business_days_between(start, end, market="US")
    assert result == 1


def test_business_days_between_jp():
    start = date(2024, 1, 8)
    end = date(2024, 1, 12)
    result = business_days_between(start, end, market="JP")
    assert isinstance(result, int)
    assert result > 0


def test_business_days_between_zero_when_start_equals_end():
    d = date(2024, 1, 8)  # Monday
    assert business_days_between(d, d, market="US") == 0
