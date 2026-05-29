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

    def fetch_earnings(self, symbol: str, limit: int = 50) -> pl.DataFrame:
        """Fetch earnings via yfinance get_earnings_dates().

        Returns EPS estimate (consensus), reported EPS, surprise%, and
        earnings announcement date/time for past quarters.

        Coverage: ~49 quarters for US stocks (back to ~2014),
                  ~88 quarters for JP stocks (back to ~2004).
        EPS estimates are analyst consensus as of announcement time.
        """
        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            ed = ticker.get_earnings_dates(limit=limit)
            if ed is None or ed.empty:
                logger.warning(f"yfinance: no earnings_dates for {symbol}")
                return _empty_earnings(symbol)

            # Keep only past quarters with reported EPS
            past = ed[ed["Reported EPS"].notna()].copy()
            if past.empty:
                logger.warning(f"yfinance: no reported EPS yet for {symbol}")
                return _empty_earnings(symbol)

            rows = []
            for dt, row in past.iterrows():
                try:
                    report_date = dt.date() if hasattr(dt, "date") else dt
                    rows.append(
                        {
                            "symbol": symbol,
                            "report_date": report_date,
                            "eps_actual": float(row["Reported EPS"])
                            if row["Reported EPS"] is not None
                            else None,
                            "eps_estimate": float(row["EPS Estimate"])
                            if row["EPS Estimate"] is not None
                            else None,
                            "surprise_pct": float(row["Surprise(%)"])
                            if row["Surprise(%)"] is not None
                            else None,
                        }
                    )
                except Exception:
                    continue

            if not rows:
                return _empty_earnings(symbol)

            logger.info(f"yfinance: fetched {len(rows)} earnings records for {symbol}")
            return pl.DataFrame(rows).cast(  # type: ignore[arg-type]
                {
                    "eps_actual": pl.Float64,
                    "eps_estimate": pl.Float64,
                    "surprise_pct": pl.Float64,
                    "report_date": pl.Date,
                }
            )
        except Exception as e:
            logger.warning(f"yfinance: earnings error for {symbol}: {e}")
            return _empty_earnings(symbol)


class YFinancePerSymbolSource(DataSource):
    """Fallback source: per-symbol yf.Ticker.history() — different API path from yf.download().

    yf.download() (batch) and Ticker.history() (per-symbol) hit different yfinance
    code paths and can have independent failure modes, providing genuine redundancy.
    Slower than the primary (one HTTP call per symbol) but requires no API key.
    """

    name = "yfinance_single"

    def fetch_ohlcv(
        self,
        symbols: list[str],
        start: date,
        end: date,
        market: str,
    ) -> pl.DataFrame:
        import pandas as pd
        import yfinance as yf

        frames = []
        for sym in symbols:

            def _fetch(s: str = sym) -> Any:
                return yf.Ticker(s).history(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    auto_adjust=False,
                )

            try:
                raw = self._retry(_fetch)
                if raw is None or raw.empty:
                    continue
                sub = raw.reset_index().copy()
                sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
                if "adj_close" not in sub.columns and "close" in sub.columns:
                    sub["adj_close"] = sub["close"]
                sub["symbol"] = sym
                sub["market"] = market
                frames.append(_select_ohlcv_cols(sub))
            except Exception as e:
                logger.warning(f"yfinance_single: failed for {sym}: {e}")

        if not frames:
            return _empty_ohlcv()

        combined = pd.concat(frames, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"]).dt.date
        combined = combined.dropna(subset=["date", "close"])
        combined["volume"] = combined["volume"].fillna(0).astype("int64")
        combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
        return pl.from_pandas(combined).cast(OHLCV_SCHEMA)  # type: ignore[arg-type]

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        import yfinance as yf

        def _download() -> Any:
            return yf.Ticker(pair).history(
                start=start.isoformat(), end=end.isoformat(), auto_adjust=False
            )

        try:
            raw = self._retry(_download)
            if raw is None or raw.empty:
                return pl.DataFrame({"date": [], "rate": []}).cast(
                    {"date": pl.Date, "rate": pl.Float64}
                )  # type: ignore[arg-type]
            df = raw.reset_index()
            df.columns = [str(c).lower() for c in df.columns]
            import pandas as _pd

            dates = _pd.to_datetime(df["date"]).dt.date.tolist()
            rates = df["close"].tolist()
            return pl.DataFrame({"date": dates, "rate": rates}).cast(
                {"date": pl.Date, "rate": pl.Float64}
            )  # type: ignore[arg-type]
        except Exception as e:
            logger.warning(f"yfinance_single: FX fetch failed: {e}")
            return pl.DataFrame({"date": [], "rate": []}).cast(
                {"date": pl.Date, "rate": pl.Float64}
            )  # type: ignore[arg-type]

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        return YFinanceSource().fetch_earnings(symbol)


class StooqSource(DataSource):
    """Fallback data source using Stooq CSV API.

    NOTE: Stooq now requires an API key for CSV downloads (as of 2025).
    Without STOOQ_API_KEY env var, requests will fail with an API key prompt.
    Set STOOQ_API_KEY to enable this source.
    """

    name = "stooq"
    _BASE = "https://stooq.com/q/d/l/"

    def _get_api_key(self) -> str | None:
        import os

        return os.environ.get("STOOQ_API_KEY")

    def _fetch_csv(self, stooq_sym: str, start: date, end: date) -> Any:
        import io
        import urllib.request

        import pandas as pd

        api_key = self._get_api_key()
        url = (
            f"{self._BASE}?s={stooq_sym}"
            f"&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}&i=d"
        )
        if api_key:
            url += f"&apikey={api_key}"

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
        if not self._get_api_key():
            logger.warning("stooq: STOOQ_API_KEY not set — skipping (API key required since 2025)")
            return _empty_ohlcv()

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
    """J-Quants V2 data source (Japan market, earnings only)."""

    name = "jquants"

    def _client(self) -> Any:
        import os

        import jquantsapi
        from dotenv import load_dotenv

        load_dotenv()
        api_key = os.getenv("JQUANTS_API_KEY")
        return jquantsapi.ClientV2(api_key=api_key)

    def fetch_ohlcv(self, symbols: list[str], start: date, end: date, market: str) -> pl.DataFrame:
        raise NotImplementedError("J-Quants OHLCV not implemented; use YFinance/Stooq")

    def fetch_fx(self, pair: str, start: date, end: date) -> pl.DataFrame:
        raise NotImplementedError("J-Quants does not provide FX data")

    def fetch_earnings_bulk(
        self,
        start: date,
        end: date,
        cache_dir: str = "data/jquants_cache",
    ) -> pl.DataFrame:
        """Fetch all JP earnings via get_fin_summary_range (one API call per calendar day, cached).

        Much faster than per-symbol fetch_earnings() for initial historical loads.
        Subsequent calls only need to fetch new dates thanks to cache_dir.
        Returns same schema as fetch_earnings(): symbol, report_date, eps_actual,
        eps_estimate, surprise_pct.
        """
        import os

        import pandas as pd

        os.makedirs(cache_dir, exist_ok=True)
        cli = self._client()

        try:
            raw = cli.get_fin_summary_range(
                start_dt=start.strftime("%Y%m%d"),
                end_dt=end.strftime("%Y%m%d"),
                cache_dir=cache_dir,
            )
        except Exception as e:
            logger.warning(f"jquants: fetch_earnings_bulk failed: {e}")
            return _empty_bulk_earnings()

        if raw.empty:
            return _empty_bulk_earnings()

        # Filter to annual/FY periods only
        if "CurPerType" in raw.columns:
            raw = raw[raw["CurPerType"].isin(["Annual", "FY"])].copy()
        else:
            raw = raw.copy()

        if raw.empty:
            return _empty_bulk_earnings()

        # Convert 5-digit J-Quants code → 4-digit + ".T"
        raw["symbol"] = raw["Code"].astype(str).str[:4] + ".T"

        # Sort per company, then shift NxFEPS to get prior-year forecast as estimate
        raw = raw.sort_values(["Code", "DiscDate"]).copy()
        if "NxFEPS" in raw.columns:
            raw["eps_estimate"] = raw.groupby("Code")["NxFEPS"].shift(1)
        else:
            raw["eps_estimate"] = float("nan")

        raw["report_date"] = pd.to_datetime(raw["DiscDate"], errors="coerce").dt.date
        raw["eps_actual"] = pd.to_numeric(
            raw["EPS"] if "EPS" in raw.columns else pd.Series(dtype=float), errors="coerce"
        )
        raw["eps_estimate"] = pd.to_numeric(raw["eps_estimate"], errors="coerce")

        # Compute surprise_pct
        mask = raw["eps_actual"].notna() & raw["eps_estimate"].notna() & (raw["eps_estimate"] != 0)
        raw["surprise_pct"] = float("nan")
        raw.loc[mask, "surprise_pct"] = (
            raw.loc[mask, "eps_actual"] - raw.loc[mask, "eps_estimate"]
        ) / raw.loc[mask, "eps_estimate"].abs()

        result = raw[["symbol", "report_date", "eps_actual", "eps_estimate", "surprise_pct"]].copy()
        result = result.dropna(subset=["report_date"])

        if result.empty:
            return _empty_bulk_earnings()

        logger.info(f"jquants bulk: {len(result)} records, {result['symbol'].nunique()} symbols")
        return pl.from_pandas(result).cast(  # type: ignore[arg-type]
            {
                "report_date": pl.Date,
                "eps_actual": pl.Float64,
                "eps_estimate": pl.Float64,
                "surprise_pct": pl.Float64,
            }
        )

    def fetch_earnings(self, symbol: str) -> pl.DataFrame:
        """Fetch earnings from J-Quants /fins/summary (annual periods only).

        symbol must be a 4-digit JP code (e.g. '7203').
        Returns rows with: symbol, report_date, eps_actual, eps_estimate, surprise_pct.

        Surprise is computed as: current FY EPS vs. prior FY's NxFEPS (next-year forecast),
        which is the standard PEAD estimate for Japanese annual reports.
        """
        # J-Quants uses 5-digit codes (4-digit + '0'); handle both.
        code = symbol.replace(".T", "")
        if len(code) == 4:
            code = code + "0"

        try:
            import time

            cli = self._client()
            time.sleep(2.0)  # J-Quants free tier rate limit（tenancy retries込みで2秒必要）
            raw = cli.get_fin_summary(code=code)
        except Exception as e:
            logger.warning(f"jquants: fetch_earnings failed for {symbol}: {e}")
            return _empty_earnings(symbol)

        if raw.empty:
            return _empty_earnings(symbol)

        # Keep only annual (FY) periods
        fy_mask = (
            raw["CurPerType"].isin(["Annual", "FY"])
            if "CurPerType" in raw.columns
            else [True] * len(raw)
        )
        df_fy = raw[fy_mask].reset_index(drop=True)

        if df_fy.empty:
            df_fy = raw.reset_index(drop=True)

        # Estimate = prior FY's NxFEPS (next-year forecast issued at prior annual announcement)
        prior_nxfeps = df_fy["NxFEPS"].shift(1) if "NxFEPS" in df_fy.columns else None

        records = []
        for i, row in df_fy.iterrows():
            try:
                report_date = date.fromisoformat(str(row["DiscDate"])[:10])
            except (ValueError, KeyError):
                continue

            eps_actual = _to_float(row.get("EPS"))
            eps_estimate = _to_float(prior_nxfeps.iloc[i]) if prior_nxfeps is not None else None  # type: ignore[union-attr]

            if eps_actual is not None and eps_estimate is not None and eps_estimate != 0:
                surprise_pct = (eps_actual - eps_estimate) / abs(eps_estimate)
            else:
                surprise_pct = None

            records.append(
                {
                    "symbol": symbol,
                    "report_date": report_date,
                    "eps_actual": eps_actual,
                    "eps_estimate": eps_estimate,
                    "surprise_pct": surprise_pct,
                }
            )

        if not records:
            return _empty_earnings(symbol)

        return pl.DataFrame(records).cast(  # type: ignore[arg-type]
            {
                "report_date": pl.Date,
                "eps_actual": pl.Float64,
                "eps_estimate": pl.Float64,
                "surprise_pct": pl.Float64,
            }
        )


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
        # JP symbols (.T suffix or 4-digit codes) → J-Quants; others → primary
        is_jp = symbol.endswith(".T") or (symbol.isdigit() and len(symbol) <= 5)
        if is_jp:
            jquants = next((s for s in self._fallbacks if isinstance(s, JQuantsSource)), None)
            if jquants is not None:
                return jquants.fetch_earnings(symbol)
        return self._primary.fetch_earnings(symbol)


# ── helpers ──────────────────────────────────────────────────────────────────


def _to_float(val: Any) -> float | None:
    """Convert a value to float, returning None for missing/empty values."""
    try:
        if val is None or (isinstance(val, float) and val != val):  # NaN check
            return None
        return float(val) if str(val).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _empty_ohlcv() -> pl.DataFrame:
    return pl.DataFrame({col: [] for col in OHLCV_SCHEMA}).cast(OHLCV_SCHEMA)  # type: ignore[arg-type]


def _empty_bulk_earnings() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": pl.Series([], dtype=pl.Utf8),
            "report_date": pl.Series([], dtype=pl.Date),
            "eps_actual": pl.Series([], dtype=pl.Float64),
            "eps_estimate": pl.Series([], dtype=pl.Float64),
            "surprise_pct": pl.Series([], dtype=pl.Float64),
        }
    )


def _empty_earnings(symbol: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol],
            "report_date": [None],
            "eps_actual": [None],
            "eps_estimate": [None],
            "surprise_pct": [None],
        }
    ).cast(  # type: ignore[arg-type]
        {
            "report_date": pl.Date,
            "eps_actual": pl.Float64,
            "eps_estimate": pl.Float64,
            "surprise_pct": pl.Float64,
        }
    )


def _normalize_yfinance(raw: Any, symbols: list[str], market: str) -> pl.DataFrame:
    """Convert yfinance DataFrame (MultiIndex or flat) to common schema.

    yfinance always returns MultiIndex columns as of v0.2.x, even for a
    single symbol. We extract each symbol's slice and normalise column names.
    """
    import pandas as pd

    frames = []

    if isinstance(raw.columns, pd.MultiIndex):
        unique_syms = raw.columns.get_level_values(1).unique().tolist()
        for sym in unique_syms:
            try:
                sub = raw.xs(sym, axis=1, level=1).copy()
                sub = sub.dropna(how="all").reset_index()
                # Normalise column names: lowercase, replace spaces with _
                sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
                # adj_close might be named "adj_close" already or absent
                if "adj_close" not in sub.columns:
                    if "close" in sub.columns:
                        sub["adj_close"] = sub["close"]
                sub["symbol"] = sym
                sub["market"] = market
                frames.append(_select_ohlcv_cols(sub))
            except Exception:
                continue
    else:
        # Flat columns (legacy / single-ticker fallback)
        sub = raw.reset_index().copy()
        sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
        if "adj_close" not in sub.columns:
            if "close" in sub.columns:
                sub["adj_close"] = sub["close"]
        sub["symbol"] = symbols[0]
        sub["market"] = market
        frames.append(_select_ohlcv_cols(sub))

    if not frames:
        return _empty_ohlcv()

    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"]).dt.date
    combined = combined.dropna(subset=["date", "close"])
    combined["volume"] = combined["volume"].fillna(0).astype("int64")
    # Drop duplicate (symbol, date) keeping last
    combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")

    return pl.from_pandas(combined).cast(OHLCV_SCHEMA)  # type: ignore[arg-type]


def _select_ohlcv_cols(df: Any) -> Any:
    """Return only the OHLCV columns we need, adding nulls for any missing."""
    needed = ["symbol", "market", "date", "open", "high", "low", "close", "adj_close", "volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = None
    return df[needed]


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
                f"divergence>{tolerance * 100:.0f}%"
            )


def _notify_fallback(source_name: str) -> None:
    """Best-effort Slack notification on fallback activation."""
    try:
        import json as _json
        import os
        import urllib.request

        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
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
        "yfinance_single": YFinancePerSymbolSource(),
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
