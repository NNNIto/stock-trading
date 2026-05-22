"""Position sizing strategies (Strategy pattern for future extensibility)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PositionSizer(ABC):
    """Return the JPY capital to allocate to a single new position."""

    @abstractmethod
    def capital_for_position(self, portfolio_value: float, open_position_count: int) -> float:
        """Compute JPY allocation for one position.

        Args:
            portfolio_value: Current total portfolio value in JPY
                             (cash + mark-to-market open positions).
            open_position_count: Number of positions currently open.
        """


class FixedFractionSizer(PositionSizer):
    """Allocate a fixed fraction of portfolio value per position (plan 1 / MVP).

    Future plan 2: per-scenario fixed fractions.
    Future plan 3: ATR-based dynamic sizing.
    """

    def __init__(self, fraction: float = 0.15, max_fraction: float = 0.20) -> None:
        if not 0 < fraction <= 1:
            raise ValueError(f"fraction must be in (0, 1], got {fraction}")
        if not 0 < max_fraction <= 1:
            raise ValueError(f"max_fraction must be in (0, 1], got {max_fraction}")
        self.fraction = fraction
        self.max_fraction = max_fraction

    def capital_for_position(self, portfolio_value: float, open_position_count: int = 0) -> float:
        return portfolio_value * self.fraction


def build_sizer(settings: Any) -> PositionSizer:
    """Build sizer from settings object."""
    return FixedFractionSizer(
        fraction=settings.risk.default_position_pct,
        max_fraction=settings.risk.max_position_pct,
    )
