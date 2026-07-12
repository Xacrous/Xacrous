"""ccxt wrapper for Binance spot market data: REST klines (`get_candles`,
cache-first) and WebSocket live updates (`subscribe_live`, ticker + kline).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

import ccxt
import ccxt.pro as ccxtpro
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
_STOP_POLL_INTERVAL = 0.2
_WATCH_ERROR_BACKOFF = 1.0

# Binance's REST klines endpoint caps a single call at 1000 candles; older
# history needs pagination via `since`, walking backward one call at a time.
KLINES_PAGE_LIMIT = 1000

TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def timeframe_to_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe {timeframe!r}") from exc


OnLiveUpdate = Callable[[str, dict], None]


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
        # Explicit spot pin: ChartPilot is spot-only, execution-free analysis
        # (never futures/margin) — pin ccxt's defaultType rather than relying
        # on its own default, so a future ccxt upgrade can't silently change it.
        config: dict = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
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

    def get_candles_before(self, symbol: str, timeframe: str, before_open_time_ms: int, limit: int = KLINES_PAGE_LIMIT) -> pd.DataFrame:
        """Return up to `limit` candles strictly older than
        `before_open_time_ms`, cache-first, oldest to newest — one page of
        the infinite scroll-back the chart uses to walk further into
        history than a single 1000-candle fetch reaches.

        Unlike `get_candles`, there's no freshness TTL here: a candle that
        already closed before `before_open_time_ms` never changes, so once
        it's cached it's cached for good.
        """
        unified_symbol = self.validate_symbol(symbol)
        cache_key = unified_symbol.replace("/", "")

        cached = self.cache.get_before(cache_key, timeframe, before_open_time_ms, limit)
        if cached is not None and len(cached) >= limit:
            return cached

        interval_ms = timeframe_to_seconds(timeframe) * 1000
        since = before_open_time_ms - limit * interval_ms
        raw = self._fetch_ohlcv_with_retry(unified_symbol, timeframe, limit, since=since)
        df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df
        self.cache.upsert(cache_key, timeframe, df)
        df = df[df["open_time"] < before_open_time_ms].reset_index(drop=True)
        return df.tail(limit).reset_index(drop=True)

    def subscribe_live(self, symbol: str, timeframe: str, on_update: OnLiveUpdate, stop_event: threading.Event) -> None:
        """Blocking call: opens a Binance WebSocket connection and streams
        ticker (tier-1, sub-second) and kline (tier-2, candle-close) updates
        via ccxt.pro's watch* methods until `stop_event` is set.

        `on_update(kind, payload)` is invoked with kind "ticker" or "kline"
        for each message, or "error" if a watch loop hits a transient
        failure (the loop backs off and keeps retrying rather than dying).
        Intended to be run on a background thread — see
        `ui.analysis_view._LiveFeedThread`.
        """
        unified_symbol = self.validate_symbol(symbol)
        asyncio.run(self._run_live_feed(unified_symbol, timeframe, on_update, stop_event))

    async def _run_live_feed(self, unified_symbol: str, timeframe: str, on_update: OnLiveUpdate, stop_event: threading.Event) -> None:
        pro_exchange = ccxtpro.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        tasks = [
            asyncio.create_task(self._watch_ticker_loop(pro_exchange, unified_symbol, on_update, stop_event)),
            asyncio.create_task(self._watch_kline_loop(pro_exchange, unified_symbol, timeframe, on_update, stop_event)),
        ]
        try:
            while not stop_event.is_set():
                await asyncio.sleep(_STOP_POLL_INTERVAL)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await pro_exchange.close()

    async def _watch_ticker_loop(self, pro_exchange, unified_symbol: str, on_update: OnLiveUpdate, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                ticker = await pro_exchange.watch_ticker(unified_symbol)
                on_update("ticker", {"last": ticker.get("last"), "percentage": ticker.get("percentage"),
                                      "quote_volume": ticker.get("quoteVolume")})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — keep the feed alive across transient WS errors
                logger.warning("Ticker WS error for %s: %s", unified_symbol, exc)
                on_update("error", {"message": str(exc)})
                await asyncio.sleep(_WATCH_ERROR_BACKOFF)

    async def _watch_kline_loop(self, pro_exchange, unified_symbol: str, timeframe: str, on_update: OnLiveUpdate, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                ohlcv = await pro_exchange.watch_ohlcv(unified_symbol, timeframe)
                if ohlcv:
                    t, o, h, low, c, v = ohlcv[-1]
                    on_update("kline", {"open_time": t, "open": o, "high": h, "low": low, "close": c, "volume": v})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — keep the feed alive across transient WS errors
                logger.warning("Kline WS error for %s %s: %s", unified_symbol, timeframe, exc)
                on_update("error", {"message": str(exc)})
                await asyncio.sleep(_WATCH_ERROR_BACKOFF)

    def _fetch_ohlcv_with_retry(self, unified_symbol: str, timeframe: str, limit: int, since: int | None = None) -> list:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            self.rate_limiter.acquire(weight=_KLINES_WEIGHT)
            try:
                return self.exchange.fetch_ohlcv(unified_symbol, timeframe=timeframe, limit=limit, since=since)
            except ccxt.RateLimitExceeded as exc:
                delay = self.rate_limiter.on_rate_limited(attempt)
                logger.warning("Rate limited fetching %s %s, backing off %.1fs", unified_symbol, timeframe, delay)
                last_error = exc
            except ccxt.NetworkError as exc:
                delay = self.rate_limiter.on_rate_limited(attempt, base_delay=0.5)
                logger.warning("Network error fetching %s %s, retrying in %.1fs: %s", unified_symbol, timeframe, delay, exc)
                last_error = exc
        raise last_error or RuntimeError("fetch_ohlcv failed with no recorded error")
