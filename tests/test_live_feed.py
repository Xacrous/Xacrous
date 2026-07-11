import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from chartpilot.data_fetcher.cache import CandleCache
from chartpilot.data_fetcher.exchange_client import ExchangeClient


@pytest.fixture
def client(tmp_path):
    cache = CandleCache(tmp_path / "cache.sqlite3")
    c = ExchangeClient(cache)
    c.exchange = MagicMock()
    c.exchange.markets = {"BTC/USDT": {}}
    c._markets_loaded = True
    yield c
    cache.close()


def _make_fake_pro(ticker_delay=0.02, kline_delay=0.03):
    fake_pro = MagicMock()
    counts = {"ticker": 0, "kline": 0}

    async def fake_watch_ticker(symbol):
        counts["ticker"] += 1
        await asyncio.sleep(ticker_delay)
        return {"last": 100.0 + counts["ticker"], "percentage": 1.0, "quoteVolume": 1000}

    async def fake_watch_ohlcv(symbol, timeframe):
        counts["kline"] += 1
        await asyncio.sleep(kline_delay)
        return [[1_700_000_000_000, 100, 101, 99, 100.5, 50]]

    async def fake_close():
        pass

    fake_pro.watch_ticker = fake_watch_ticker
    fake_pro.watch_ohlcv = fake_watch_ohlcv
    fake_pro.close = fake_close
    return fake_pro, counts


def test_subscribe_live_streams_ticker_and_kline_events(client):
    events = []
    stop_event = threading.Event()
    fake_pro, counts = _make_fake_pro()

    with patch("chartpilot.data_fetcher.exchange_client.ccxtpro.binance", return_value=fake_pro):
        t = threading.Thread(
            target=client.subscribe_live, args=("BTCUSDT", "1m", lambda kind, payload: events.append((kind, payload)), stop_event)
        )
        t.start()
        time.sleep(0.3)
        stop_event.set()
        t.join(timeout=5)

    assert not t.is_alive()
    kinds = {kind for kind, _ in events}
    assert "ticker" in kinds
    assert "kline" in kinds
    assert counts["ticker"] > 0
    assert counts["kline"] > 0


def test_subscribe_live_stops_promptly(client):
    stop_event = threading.Event()
    fake_pro, _ = _make_fake_pro(ticker_delay=0.01, kline_delay=0.01)

    with patch("chartpilot.data_fetcher.exchange_client.ccxtpro.binance", return_value=fake_pro):
        t = threading.Thread(target=client.subscribe_live, args=("BTCUSDT", "1m", lambda k, p: None, stop_event))
        t.start()
        time.sleep(0.1)
        start = time.monotonic()
        stop_event.set()
        t.join(timeout=5)
        elapsed = time.monotonic() - start

    assert not t.is_alive()
    assert elapsed < 2.0


def test_subscribe_live_validates_symbol_before_connecting(client):
    stop_event = threading.Event()
    with pytest.raises(Exception):
        client.subscribe_live("NOTREAL", "1m", lambda k, p: None, stop_event)
