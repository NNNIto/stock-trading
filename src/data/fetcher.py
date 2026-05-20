"""Data source abstraction layer with YFinance and Stooq providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import polars as pl

from src.utils.logger import get_logger

logger = get_logger()

# Common output schema columns
OHLCV_SCHEMA = {
    "symbol": pl.Utf8,
    "market": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "adj_close": pl.Float64,
    "volume": pl.Int64,
}


class DataSourceError(Exception):
    """Raised when a data source fails after all retries."""


class DataSource(ABC):
    """Abstract base class for market data providers."""

    name: str = "base"

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbols: list[str],
        start: date,
        end: date,
        market: str,
    ) -> pl.DataFrame:
        """Fetch OHLCV data for given symbols and date range.

        Returns DataFrame with OHLCV_SCHEMA columns.
        """

    @abstractmethod
    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        """Fetch FX rate (e.g. 'USDJPY=X') for given date range.

        Returns DataFrame with columns: date, rate.
        """

    @abstractmethod
    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        """Fetch earnings data for a symbol.

        Returns DataFrame with columns: symbol, report_date, eps_actual, eps_estimate.
        """

    def _retry(self, fn: Any, *args: Any, attempts: int = 3, **kwargs: Any) -> Any:
        """Call fn with exponential backoff retries."""
        for attempt in range(1, attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if attempt == attempts:
                    raise DataSourceError(
                        f"{self.name}: failed after {attempts} attempts: {e}"
                    ) from e
                wait = 2**attempt
                logger.warning(f"{self.name}: attempt {attempt} failed ({e}), retrying in {wait}s")
                time.sleep(wait)


class YFinanceSource(DataSource):
    """Primary data source using yfinance."""

    name = "yfinance"

    def fetch_ohlcv(
        self,
        symbols: list[str],
        start: date,
        end: date,
        market: str,
    ) -> pl.DataFrame:
        import yfinance as yf

        def _download() -> Any:
            return yf.download(
                symbols,
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=False,
                progress=False,
                threads=True,
            )

        raw = self._retry(_download)
        if raw is None or raw.empty:
            return _empty_ohlcv()

        return _normalize_yfinance(raw, symbols, market)

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        import yfinance as yf

        def _download() -> Any:
            ticker = yf.Ticker(pair)
            return ticker.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)

        raw = self._retry(_download)
        if raw is None or raw.empty:
            return pl.DataFrame({"date": [], "rate": []}).cast(
                {"date": pl.Date, "rate": pl.Float64}
            )  # type: ignore[arg-type]

        import pandas as pd

        df = raw.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        dates = pd.to_datetime(df["date"]).dt.date.tolist()
        rates = df["close"].tolist()
        return pl.DataFrame({"date": dates, "rate": rates}).cast(
            {"date": pl.Date, "rate": pl.Float64}
        )  # type: ignore[arg-type]

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        """Fetch earnings via yfinance. Coverage is limited (typically ~4 quarters).

        WARNING: EPS estimates are not reliably available through yfinance.
        S4/PEAD implementation may need simplified fallback (gap+volume only).
        """
        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            income = ticker.quarterly_income_stmt
            if income is None or income.empty:
                logger.warning(f"yfinance: no earnings data for {symbol}")
                return _empty_earnings(symbol)

            # Extract basic EPS if available
            rows = []
            for col in income.columns:
                try:
                    report_date = col.date() if hasattr(col, "date") else col
                    eps_row = income.loc["Basic EPS"] if "Basic EPS" in income.index else None
                    eps_actual = float(eps_row[col]) if eps_row is not None else None
                    rows.append(
                        {
                            "symbol": symbol,
                            "report_date": report_date,
                            "eps_actual": eps_actual,
                            "eps_estimate": None,  # Not available via yfinance
                        }
                    )
                except Exception:
                    continue

            if not rows:
                logger.warning(f"yfinance: earnings parse failed for {symbol}")
                return _empty_earnings(symbol)

            logger.info(
                f"yfinance: fetched {len(rows)} earnings records for {symbol} (eps_estimate unavailable)"
            )
            return pl.DataFrame(rows).cast(
                {  # type: ignore[arg-type]
                    "eps_actual": pl.Float64,
                    "eps_estimate": pl.Float64,
                    "report_date": pl.Date,
                }
            )
        except Exception as e:
            logger.warning(f"yfinance: earnings error for {symbol}: {e}")
            return _empty_earnings(symbol)


class StooqSource(DataSource):
    """Fallback data source using Stooq CSV API (free, no API key, no pandas_datareader)."""

    name = "stooq"
    _BASE = "https://stooq.com/q/d/l/"

    def _fetch_csv(self, stooq_sym: str, start: date, end: date) -> Any:
        import io
        import urllib.request

        import pandas as pd

        url = (
            f"{self._BASE}?s={stooq_sym}"
            f"&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}&i=d"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; stock-trading-bot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()
        return pd.read_csv(io.StringIO(content))

    def fetch_ohlcv(
        self,
        symbols: list[str],
        start: date,
        end: date,
        market: str,
    ) -> pl.DataFrame:
        import pandas as pd

        frames = []
        for sym in symbols:
            stooq_sym = _to_stooq_symbol(sym, market)
            try:

                def _fetch(s: str = stooq_sym) -> Any:
                    return self._fetch_csv(s, start, end)

                raw = self._retry(_fetch)
                if raw is None or raw.empty:
                    continue
                raw.columns = [str(c).lower() for c in raw.columns]
                raw["symbol"] = sym
                raw["market"] = market
                raw["adj_close"] = raw["close"]  # Stooq doesn't provide adjusted close
                raw["date"] = pd.to_datetime(raw["date"]).dt.date
                raw["volume"] = raw["volume"].fillna(0).astype("int64")
                frames.append(
                    raw[
                        [
                            "symbol",
                            "market",
                            "date",
                            "open",
                            "high",
                            "low",
                            "close",
                            "adj_close",
                            "volume",
                        ]
                    ]
                )
            except Exception as e:
                logger.warning(f"stooq: failed for {sym}: {e}")

        if not frames:
            return _empty_ohlcv()

        combined = pd.concat(frames, ignore_index=True)
        return pl.from_pandas(combined).cast(OHLCV_SCHEMA)  # type: ignore[arg-type]

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        import pandas as pd

        stooq_pair = pair.replace("=X", "").upper()
        try:

            def _fetch() -> Any:
                return self._fetch_csv(stooq_pair, start, end)

            raw = self._retry(_fetch)
            if raw is None or raw.empty:
                return pl.DataFrame({"date": [], "rate": []}).cast(
                    {"date": pl.Date, "rate": pl.Float64}
                )  # type: ignore[arg-type]
            raw.columns = [str(c).lower() for c in raw.columns]
            dates = pd.to_datetime(raw["date"]).dt.date.tolist()
            rates = raw["close"].tolist()
            return pl.DataFrame({"date": dates, "rate": rates}).cast(
                {"date": pl.Date, "rate": pl.Float64}
            )  # type: ignore[arg-type]
        except Exception as e:
            logger.warning(f"stooq: FX fetch failed for {pair}: {e}")
            return pl.DataFrame({"date": [], "rate": []}).cast(
                {"date": pl.Date, "rate": pl.Float64}
            )  # type: ignore[arg-type]

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        logger.warning(f"stooq: earnings data not available for {symbol}")
        return _empty_earnings(symbol)


class JQuantsSource(DataSource):
    """Stub for J-Quants (Japan market, requires registration)."""

    name = "jquants"

    def fetch_ohlcv(self, symbols: list[str], start: date, end: date, market: str) -> pl.DataFrame:
        # TODO: implement using jquants-api-client after registration
        raise NotImplementedError("J-Quants requires account registration")

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        raise NotImplementedError("J-Quants does not provide FX data")

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        # TODO: J-Quants provides earnings via /fins/statements endpoint
        raise NotImplementedError("J-Quants earnings not yet implemented")


class AlphaVantageSource(DataSource):
    """Stub for Alpha Vantage (US market, requires API key)."""

    name = "alphavantage"

    def fetch_ohlcv(self, symbols: list[str], start: date, end: date, market: str) -> pl.DataFrame:
        # TODO: implement using ALPHA_VANTAGE_API_KEY env var
        raise NotImplementedError("Alpha Vantage requires API key")

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        raise NotImplementedError("Alpha Vantage FX not yet implemented")

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        raise NotImplementedError("Alpha Vantage earnings not yet implemented")


class FallbackDataSource:
    """Orchestrates primary + fallback data sources with cross-check."""

    def __init__(
        self,
        primary: DataSource,
        fallbacks: list[DataSource],
        cross_check_enabled: bool = True,
        cross_check_tolerance_pct: float = 0.02,
    ) -> None:
        self._primary = primary
        self._fallbacks = fallbacks
        self._cross_check_enabled = cross_check_enabled
        self._cross_check_tolerance_pct = cross_check_tolerance_pct

    def fetch_ohlcv(
        self,
        symbols: list[str],
        start: date,
        end: date,
        market: str,
    ) -> pl.DataFrame:
        """Fetch with automatic fallback on primary failure."""
        sources = [self._primary] + self._fallbacks
        last_error: Exception | None = None

        for source in sources:
            try:
                result = source.fetch_ohlcv(symbols, start, end, market)
                if result.is_empty():
                    raise DataSourceError(f"{source.name}: returned empty data")

                if source is not self._primary:
                    logger.warning(
                        f"Fallback activated: using {source.name} instead of {self._primary.name}"
                    )
                    _notify_fallback(source.name)

                return result
            except Exception as e:
                logger.warning(f"{source.name}: fetch failed: {e}")
                last_error = e

        raise DataSourceError(f"All sources failed. Last error: {last_error}")

    def fetch_ohlcv_with_cross_check(
        self,
        symbols: list[str],
        start: date,
        end: date,
        market: str,
    ) -> pl.DataFrame:
        """Fetch from primary, optionally cross-check with first fallback."""
        primary_data = self._primary.fetch_ohlcv(symbols, start, end, market)

        if self._cross_check_enabled and self._fallbacks:
            try:
                fallback_data = self._fallbacks[0].fetch_ohlcv(symbols, start, end, market)
                _cross_check(primary_data, fallback_data, self._cross_check_tolerance_pct)
            except Exception as e:
                logger.warning(f"Cross-check skipped: {e}")

        return primary_data

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        sources = [self._primary] + self._fallbacks
        for source in sources:
            try:
                result = source.fetch_fx(pair, start, end)
                if not result.is_empty():
                    return result
            except Exception as e:
                logger.warning(f"{source.name}: FX fetch failed: {e}")
        return pl.DataFrame({"date": [], "rate": []}).cast({"date": pl.Date, "rate": pl.Float64})  # type: ignore[arg-type]

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        return self._primary.fetch_earnings(symbol)


# ── helpers ──────────────────────────────────────────────────────────────────


def _empty_ohlcv() -> pl.DataFrame:
    return pl.DataFrame({col: [] for col in OHLCV_SCHEMA}).cast(OHLCV_SCHEMA)  # type: ignore[arg-type]


def _empty_earnings(symbol: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol],
            "report_date": [None],
            "eps_actual": [None],
            "eps_estimate": [None],
        }
    ).cast({"report_date": pl.Date, "eps_actual": pl.Float64, "eps_estimate": pl.Float64})


def _normalize_yfinance(raw: Any, symbols: list[str], market: str) -> pl.DataFrame:
    """Convert yfinance multi-ticker DataFrame to common schema."""
    import pandas as pd

    # yfinance returns MultiIndex columns when multiple symbols
    if isinstance(raw.columns, pd.MultiIndex):
        rows = []
        unique_syms = raw.columns.get_level_values(1).unique()
        for sym in unique_syms:
            try:
                sub = raw.xs(sym, axis=1, level=1).dropna(how="all").reset_index()
                sub.columns = [str(c).lower() for c in sub.columns]
                sub["symbol"] = sym
                sub["market"] = market
                sub["adj_close"] = sub.get("adj close", sub.get("close", sub["close"]))
                rows.append(sub)
            except Exception:
                continue
        if not rows:
            return _empty_ohlcv()
        combined = pd.concat(rows, ignore_index=True)
    else:
        # Single symbol
        raw = raw.reset_index()
        raw.columns = [str(c).lower() for c in raw.columns]
        raw["symbol"] = symbols[0]
        raw["market"] = market
        raw["adj_close"] = raw.get("adj close", raw.get("close", raw["close"]))
        combined = raw

    combined = combined.rename(columns={"adj close": "adj_close"})
    combined["date"] = pd.to_datetime(combined["date"]).dt.date

    needed = ["symbol", "market", "date", "open", "high", "low", "close", "adj_close", "volume"]
    for col in needed:
        if col not in combined.columns:
            combined[col] = None

    combined = combined[needed].dropna(subset=["date", "close"])
    combined["volume"] = combined["volume"].fillna(0).astype("int64")

    return pl.from_pandas(combined).cast(OHLCV_SCHEMA)  # type: ignore[arg-type]


def _to_stooq_symbol(symbol: str, market: str) -> str:
    """Convert to Stooq symbol format."""
    if market == "JP":
        code = symbol.replace(".T", "")
        return f"{code}.JP"
    return symbol.upper()


def _cross_check(primary: pl.DataFrame, fallback: pl.DataFrame, tolerance: float) -> None:
    """Warn when close prices deviate beyond tolerance between sources."""
    if primary.is_empty() or fallback.is_empty():
        return

    joined = primary.join(fallback, on=["symbol", "date"], suffix="_fb", how="inner")
    if joined.is_empty():
        return

    diverged = joined.filter(
        ((pl.col("close") - pl.col("close_fb")).abs() / pl.col("close_fb")) > tolerance
    )
    if not diverged.is_empty():
        for row in diverged.iter_rows(named=True):
            logger.warning(
                f"Cross-check: {row['symbol']} on {row['date']}: "
                f"primary={row['close']:.2f} fallback={row['close_fb']:.2f} "
                f"divergence>{tolerance*100:.0f}%"
            )


def _notify_fallback(source_name: str) -> None:
    """Best-effort Slack notification on fallback activation."""
    try:
        import json as _json
        import os
        import urllib.request

        url = os.environ.get("SLACK_WEBHOOK_URL")
        if not url:
            return
        payload = _json.dumps(
            {"text": f":warning: Data source fallback activated: using {source_name}"}
        ).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, payload, {"Content-Type": "application/json"})
        )
    except Exception:
        pass


def build_default_source(settings: Any | None = None) -> FallbackDataSource:
    """Build FallbackDataSource from settings."""
    if settings is None:
        from src.utils.config import get_settings

        settings = get_settings()

    cfg = settings.data.sources
    primary = YFinanceSource()
    fallback_map: dict[str, DataSource] = {
        "stooq": StooqSource(),
        "jquants": JQuantsSource(),
        "alphavantage": AlphaVantageSource(),
    }
    fallbacks = [fallback_map[name] for name in cfg.fallback_order if name in fallback_map]

    return FallbackDataSource(
        primary=primary,
        fallbacks=fallbacks,
        cross_check_enabled=cfg.cross_check_enabled,
        cross_check_tolerance_pct=cfg.cross_check_tolerance_pct,
    )
