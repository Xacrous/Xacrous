import pytest

from chartpilot.data_fetcher.cache import CandleCache
from chartpilot.data_fetcher.exchange_client import ExchangeClient, timeframe_to_seconds


@pytest.fixture
def client(tmp_path):
    cache = CandleCache(tmp_path / "cache.sqlite3")
    c = ExchangeClient(cache)
    c.exchange = MagicMockWithMarkets()
    c._markets_loaded = True
    yield c
    cache.close()


class MagicMockWithMarkets:
    """Minimal fetch_ohlcv stub — avoids importing unittest.mock at module
    scope just for a markets dict + a call-recording fetch_ohlcv."""

    def __init__(self):
        self.markets = {"BTC/USDT": {}}
        self.calls: list[dict] = []
        self.response: list = []

    def fetch_ohlcv(self, symbol, timeframe=None, limit=None, since=None):
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since})
        return self.response


def _raw_candles(n, start_ms, step_ms):
    return [[start_ms + i * step_ms, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0 + i] for i in range(n)]


def test_timeframe_to_seconds_known():
    assert timeframe_to_seconds("1h") == 3600
    assert timeframe_to_seconds("1d") == 86400


def test_timeframe_to_seconds_unknown_raises():
    with pytest.raises(ValueError):
        timeframe_to_seconds("7h")


def test_get_candles_before_fetches_with_since_and_filters(client):
    before_ms = 10 * 3_600_000
    client.exchange.response = _raw_candles(5, start_ms=5 * 3_600_000, step_ms=3_600_000)

    result = client.get_candles_before("BTCUSDT", "1h", before_open_time_ms=before_ms, limit=5)

    assert len(client.exchange.calls) == 1
    call = client.exchange.calls[0]
    assert call["since"] == before_ms - 5 * 3_600_000
    assert list(result["open_time"]) == [i * 3_600_000 for i in range(5, 10)]
    assert (result["open_time"] < before_ms).all()
    assert result["open_time"].is_monotonic_increasing


def test_get_candles_before_is_cache_first(client):
    before_ms = 10 * 3_600_000
    client.exchange.response = _raw_candles(5, start_ms=5 * 3_600_000, step_ms=3_600_000)
    client.get_candles_before("BTCUSDT", "1h", before_open_time_ms=before_ms, limit=5)
    assert len(client.exchange.calls) == 1

    # second call for the same older window should be served from cache, no new fetch
    client.get_candles_before("BTCUSDT", "1h", before_open_time_ms=before_ms, limit=5)
    assert len(client.exchange.calls) == 1


def test_get_candles_before_empty_response(client):
    client.exchange.response = []
    result = client.get_candles_before("BTCUSDT", "1h", before_open_time_ms=1_000_000, limit=5)
    assert result.empty
