"""Tests for position sizer."""

from __future__ import annotations

import pytest

from src.portfolio.sizer import FixedFractionSizer


def test_fixed_fraction_returns_fraction_of_capital():
    sizer = FixedFractionSizer(fraction=0.15)
    assert sizer.capital_for_position(1_000_000) == pytest.approx(150_000)


def test_fixed_fraction_ignores_open_position_count():
    sizer = FixedFractionSizer(fraction=0.15)
    assert sizer.capital_for_position(1_000_000, open_position_count=5) == pytest.approx(150_000)


def test_fixed_fraction_scales_with_portfolio_value():
    sizer = FixedFractionSizer(fraction=0.10)
    assert sizer.capital_for_position(2_000_000) == pytest.approx(200_000)


def test_invalid_fraction_raises():
    with pytest.raises(ValueError):
        FixedFractionSizer(fraction=0.0)
    with pytest.raises(ValueError):
        FixedFractionSizer(fraction=1.1)
