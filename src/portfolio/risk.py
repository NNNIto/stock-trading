"""Risk manager: portfolio-level circuit breakers and position guards."""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.logger import get_logger

logger = get_logger()


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str  # empty string when allowed=True


class RiskManager:
    """Evaluate portfolio-level risk constraints before new entries.

    Checks (in priority order):
    1. Portfolio drawdown circuit breaker (halt ALL new entries)
    2. Macro filter (VIX, Nikkei)
    3. Max open positions
    4. Sector concentration (max 3 per sector)
    """

    def __init__(
        self,
        max_positions: int = 7,
        max_sector_positions: int = 3,
        portfolio_dd_threshold: float = -0.20,
        vix_threshold: float = 35.0,
        nikkei_dd_threshold: float = -0.10,
    ) -> None:
        self.max_positions = max_positions
        self.max_sector_positions = max_sector_positions
        self.portfolio_dd_threshold = portfolio_dd_threshold
        self.vix_threshold = vix_threshold
        self.nikkei_dd_threshold = nikkei_dd_threshold

    def check_circuit_breaker(
        self,
        current_value: float,
        peak_value: float,
    ) -> RiskDecision:
        """Block all new entries if portfolio drawdown exceeds threshold."""
        if peak_value <= 0:
            return RiskDecision(True, "")
        dd = (current_value - peak_value) / peak_value
        if dd <= self.portfolio_dd_threshold:
            msg = f"circuit breaker: portfolio DD {dd:.1%} < {self.portfolio_dd_threshold:.0%}"
            logger.error(f"RISK: {msg}")
            return RiskDecision(False, msg)
        return RiskDecision(True, "")

    def check_macro(
        self,
        vix: float | None = None,
        nikkei_daily_return: float | None = None,
    ) -> RiskDecision:
        """Block new entries under extreme macro conditions."""
        if vix is not None and vix > self.vix_threshold:
            msg = f"macro: VIX {vix:.1f} > {self.vix_threshold}"
            logger.warning(f"RISK: {msg}")
            return RiskDecision(False, msg)
        if nikkei_daily_return is not None and nikkei_daily_return < self.nikkei_dd_threshold:
            msg = f"macro: Nikkei {nikkei_daily_return:.1%} < {self.nikkei_dd_threshold:.0%}"
            logger.warning(f"RISK: {msg}")
            return RiskDecision(False, msg)
        return RiskDecision(True, "")

    def check_position_limit(self, n_open: int) -> RiskDecision:
        """Block new entries when portfolio is at max capacity."""
        if n_open >= self.max_positions:
            msg = f"position limit: {n_open}/{self.max_positions} positions open"
            return RiskDecision(False, msg)
        return RiskDecision(True, "")

    def check_sector_concentration(
        self,
        new_symbol_sector: str | None,
        open_sector_counts: dict[str, int],
    ) -> RiskDecision:
        """Block if sector would exceed concentration limit."""
        if new_symbol_sector is None:
            return RiskDecision(True, "")  # unknown sector → allow
        current = open_sector_counts.get(new_symbol_sector, 0)
        if current >= self.max_sector_positions:
            msg = (
                f"sector: {new_symbol_sector} already has "
                f"{current}/{self.max_sector_positions} positions"
            )
            return RiskDecision(False, msg)
        return RiskDecision(True, "")

    def assess_new_entry(
        self,
        symbol: str,
        symbol_sector: str | None,
        n_open: int,
        open_sector_counts: dict[str, int],
        portfolio_value: float,
        peak_value: float,
        vix: float | None = None,
        nikkei_daily_return: float | None = None,
    ) -> RiskDecision:
        """Run all checks for a prospective new entry; return first failure."""
        for check in [
            self.check_circuit_breaker(portfolio_value, peak_value),
            self.check_macro(vix, nikkei_daily_return),
            self.check_position_limit(n_open),
            self.check_sector_concentration(symbol_sector, open_sector_counts),
        ]:
            if not check.allowed:
                return check
        return RiskDecision(True, "")
