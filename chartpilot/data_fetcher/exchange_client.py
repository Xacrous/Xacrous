"""ccxt wrapper for Binance spot market data.

Phase 1 scope: REST klines only (`get_candles`), cache-first reads, and
symbol validation against exchange metadata. WebSocket live updates
(`subscribe_live`) land in Phase 2 per the roadmap.
"""

from __future__ import annotations

import logging

import ccxt
import pandas as pd

from chartpilot.data_fetcher.cache import CandleCache
from chartpilot.data_fetcher.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

# Binance spot REST weight cap is read from /api/v3/exchangeInfo at runtime
# (Section 2.3); this is only the conservative fallback used before that
# first call succeeds.
_DEFAULT_BUCKET_CAPACITY = 1000.0
_DEFAULT_REFILL_PER_SECOND = 1000.0 / 60.0
_KLINES_WEIGHT = 2.0
_MAX_RETRIES = 5


class SymbolNotFoundError(ValueError):
    """Raised when a requested symbol isn't a valid Binance spot market."""


class ExchangeClient:
    def __init__(
        self,
        cache: CandleCache,
        api_key: str | None = None,
        api_secret: str | None = None,
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        config: dict = {"enableRateLimit": True}
        if api_key and api_secret:
            config["apiKey"] = api_key
            config["secret"] = api_secret
        self.exchange = ccxt.binance(config)
        self.rate_limiter = TokenBucketRateLimiter(
            _DEFAULT_BUCKET_CAPACITY, _DEFAULT_REFILL_PER_SECOND
        )
        self._markets_loaded = False

    def _ensure_markets(self) -> None:
        if self._markets_loaded:
            return
        self.rate_limiter.acquire(weight=10.0)
        self.exchange.load_markets()
        self._markets_loaded = True

    def validate_symbol(self, symbol: str) -> str:
        """Normalize and validate a symbol against exchange metadata.

        Accepts both "BTCUSDT" and "BTC/USDT" and returns the ccxt unified
        form. Raises SymbolNotFoundError for anything not a listed Binance
        spot market, so the fetch pipeline never has to handle a malformed
        or delisted symbol downstream.
        """
        self._ensure_markets()
        candidate = symbol.strip().upper()
        if "/" not in candidate and candidate.endswith("USDT"):
            candidate = f"{candidate[:-4]}/USDT"
        if candidate not in self.exchange.markets:
            raise SymbolNotFoundError(f"{symbol!r} is not a valid Binance spot symbol")
        return candidate

    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
        """Return `limit` OHLCV candles, cache-first, oldest to newest."""
        unified_symbol = self.validate_symbol(symbol)
        cache_key = unified_symbol.replace("/", "")

        cached = self.cache.get(cache_key, timeframe, limit, self.cache_ttl_seconds)
        if cached is not None and len(cached) >= limit:
            return cached.tail(limit).reset_index(drop=True)

        raw = self._fetch_ohlcv_with_retry(unified_symbol, timeframe, limit)
        df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume"])
        self.cache.upsert(cache_key, timeframe, df)
        return df.tail(limit).reset_index(drop=True)

    def _fetch_ohlcv_with_retry(self, unified_symbol: str, timeframe: str, limit: int) -> list:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            self.rate_limiter.acquire(weight=_KLINES_WEIGHT)
            try:
                return self.exchange.fetch_ohlcv(unified_symbol, timeframe=timeframe, limit=limit)
            except ccxt.RateLimitExceeded as exc:
                delay = self.rate_limiter.on_rate_limited(attempt)
                logger.warning("Rate limited fetching %s %s, backing off %.1fs", unified_symbol, timeframe, delay)
                last_error = exc
            except ccxt.NetworkError as exc:
                delay = self.rate_limiter.on_rate_limited(attempt, base_delay=0.5)
                logger.warning("Network error fetching %s %s, retrying in %.1fs: %s", unified_symbol, timeframe, delay, exc)
                last_error = exc
        raise last_error or RuntimeError("fetch_ohlcv failed with no recorded error")
