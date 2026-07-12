import time

import pandas as pd
import pytest

from chartpilot.data_fetcher.cache import CandleCache


@pytest.fixture
def cache(tmp_path):
    c = CandleCache(tmp_path / "cache.sqlite3")
    yield c
    c.close()


def _candles(n=5, start=0):
    return pd.DataFrame({
        "open_time": [start + i * 3600_000 for i in range(n)],
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [10.0 + i for i in range(n)],
    })


def test_get_returns_none_when_empty(cache):
    assert cache.get("BTCUSDT", "4h", limit=5, ttl_seconds=60) is None


def test_upsert_then_get_round_trip(cache):
    df = _candles(5)
    cache.upsert("BTCUSDT", "4h", df)
    result = cache.get("BTCUSDT", "4h", limit=5, ttl_seconds=60)
    assert result is not None
    assert len(result) == 5
    assert list(result["open_time"]) == list(df["open_time"])
    assert result["close"].iloc[-1] == df["close"].iloc[-1]


def test_get_respects_limit(cache):
    cache.upsert("BTCUSDT", "4h", _candles(10))
    result = cache.get("BTCUSDT", "4h", limit=3, ttl_seconds=60)
    assert len(result) == 3
    # should be the most recent 3, oldest-to-newest
    assert result["open_time"].is_monotonic_increasing


def test_get_returns_none_when_stale(cache):
    cache.upsert("BTCUSDT", "4h", _candles(5))
    assert cache.get("BTCUSDT", "4h", limit=5, ttl_seconds=0) is None


def test_upsert_updates_existing_candle(cache):
    df = _candles(3)
    cache.upsert("BTCUSDT", "4h", df)
    df.loc[df.index[-1], "close"] = 999.0
    cache.upsert("BTCUSDT", "4h", df)
    result = cache.get("BTCUSDT", "4h", limit=3, ttl_seconds=60)
    assert result["close"].iloc[-1] == 999.0
    assert len(result) == 3  # no duplicate row


def test_different_symbols_and_timeframes_are_isolated(cache):
    cache.upsert("BTCUSDT", "4h", _candles(3))
    cache.upsert("ETHUSDT", "4h", _candles(3, start=100))
    cache.upsert("BTCUSDT", "1h", _candles(3, start=200))
    assert cache.get("BTCUSDT", "4h", limit=3, ttl_seconds=60) is not None
    assert cache.get("ETHUSDT", "4h", limit=3, ttl_seconds=60) is not None
    assert cache.get("BTCUSDT", "1h", limit=3, ttl_seconds=60) is not None
    assert cache.get("SOLUSDT", "4h", limit=3, ttl_seconds=60) is None


def test_get_before_returns_none_when_empty(cache):
    assert cache.get_before("BTCUSDT", "4h", before_open_time_ms=999_999_999, limit=5) is None


def test_get_before_returns_only_older_candles(cache):
    df = _candles(10)  # open_time 0, 3.6e6, 7.2e6, ...
    cache.upsert("BTCUSDT", "4h", df)
    cutoff = int(df["open_time"].iloc[5])
    result = cache.get_before("BTCUSDT", "4h", before_open_time_ms=cutoff, limit=10)
    assert result is not None
    assert list(result["open_time"]) == list(df["open_time"].iloc[:5])
    assert result["open_time"].is_monotonic_increasing


def test_get_before_respects_limit(cache):
    cache.upsert("BTCUSDT", "4h", _candles(10))
    result = cache.get_before("BTCUSDT", "4h", before_open_time_ms=10_000_000, limit=3)
    assert len(result) == 3
    assert result["open_time"].is_monotonic_increasing


def test_get_before_ignores_staleness(cache):
    # get_before has no TTL concept — closed historical candles don't go stale.
    df = _candles(5)
    cache.upsert("BTCUSDT", "4h", df)
    cutoff = int(df["open_time"].iloc[-1]) + 1
    result = cache.get_before("BTCUSDT", "4h", before_open_time_ms=cutoff, limit=5)
    assert result is not None
    assert len(result) == 5
