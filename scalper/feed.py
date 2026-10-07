"""Market data feeds.

BinanceFeed  - live websocket streams: top-20 order book every 100 ms, every trade, 1m candles.
DemoFeed     - a synthetic market for trying the dashboard without a connection (paper only).
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import time

from .book import Book
from .flow import Candle, Candles, TradeFlow

MAINNET_WS = "wss://stream.binance.com:9443/stream?streams="
TESTNET_WS = "wss://stream.testnet.binance.vision/stream?streams="
STALE_S = 3.0


class FeedBase:
    def __init__(self, flow: TradeFlow, candles: Candles):
        self.flow, self.candles = flow, candles
        self.book = Book()
        self.connected = False
        self.messages = 0
        self.on_book = lambda book: None      # set by the engine (paper broker hooks)
        self.on_trade = lambda price: None
        self.error: str | None = None

    @property
    def stale(self) -> bool:
        return not self.book.ready or time.time() - self.book.ts > STALE_S

    def _set_book(self, bids, asks) -> None:
        self.book = Book([(float(p), float(q)) for p, q in bids if float(q) > 0],
                         [(float(p), float(q)) for p, q in asks if float(q) > 0], time.time())
        self.on_book(self.book)

    def _trade(self, ts, price, qty, buyer_is_maker) -> None:
        self.flow.add(ts, price, qty, buyer_is_maker)
        self.on_trade(price)


class BinanceFeed(FeedBase):
    def __init__(self, symbol_id: str, flow: TradeFlow, candles: Candles, exchange, ccxt_symbol: str,
                 testnet: bool = False):
        super().__init__(flow, candles)
        s = symbol_id.lower()
        self.url = (TESTNET_WS if testnet else MAINNET_WS) + f"{s}@depth20@100ms/{s}@aggTrade/{s}@kline_1m"
        self.ex, self.ccxt_symbol = exchange, ccxt_symbol

    async def _load_candles(self) -> None:
        rows = await self.ex.fetch_ohlcv(self.ccxt_symbol, "1m", limit=120)
        now_ms = time.time() * 1000
        for t, o, h, l, c, v in rows:
            self.candles.upsert(Candle(int(t), o, h, l, c, v or 0, t + 60_000 <= now_ms))

    async def run(self) -> None:
        import websockets
        backoff = 1
        while True:
            try:
                await self._load_candles()
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20,
                                              max_size=2**22, open_timeout=15) as ws:
                    self.connected, self.error, backoff = True, None, 1
                    async for raw in ws:
                        self._handle(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                self.error = f"{type(exc).__name__}: {exc}"
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _handle(self, msg: dict) -> None:
        self.messages += 1
        stream, d = msg.get("stream", ""), msg.get("data", {})
        if "@depth" in stream:
            self._set_book(d["bids"], d["asks"])
        elif d.get("e") == "aggTrade":
            self._trade(d["T"] / 1000, float(d["p"]), float(d["q"]), bool(d["m"]))
        elif d.get("e") == "kline":
            k = d["k"]
            self.candles.upsert(Candle(int(k["t"]), float(k["o"]), float(k["h"]), float(k["l"]),
                                       float(k["c"]), float(k["v"]), bool(k["x"])))


class DemoFeed(FeedBase):
    """Random-walk market with bursts of buying and selling pressure. For UI testing only."""

    def __init__(self, flow: TradeFlow, candles: Candles, start_price: float = 100.0, tick: float = 0.01,
                 seed: int | None = None):
        super().__init__(flow, candles)
        self.price, self.tick = start_price, tick
        self.rng = random.Random(seed)
        self.pressure = 0.0
        now = time.time()
        p = start_price
        for i in range(60, 0, -1):  # an hour of history so filters have data
            o = p
            p *= 1 + self.rng.gauss(0, 0.002)
            hi, lo = max(o, p) * 1.002, min(o, p) * 0.998
            t = int((now // 60 - i) * 60_000)
            self.candles.upsert(Candle(t, o, hi, lo, p, 10, True))
        self.price = p

    async def run(self) -> None:
        self.connected = True
        while True:
            self._tick(time.time())
            await asyncio.sleep(0.1)

    def _tick(self, now: float) -> None:
        r = self.rng
        if r.random() < 0.01:
            self.pressure = r.choice([-1, 1]) * r.uniform(0.3, 1.0)
        self.pressure *= 0.995
        self.price = max(self.tick * 10, self.price * (1 + r.gauss(self.pressure * 0.00008, 0.00025)))
        mid = self.price
        bid = math.floor(mid / self.tick) * self.tick
        ask = bid + self.tick
        tilt = 1 + self.pressure
        bids = [(bid - i * self.tick, r.uniform(0.5, 3) * tilt * (1 + i / 10)) for i in range(20)]
        asks = [(ask + i * self.tick, r.uniform(0.5, 3) * (2 - tilt) * (1 + i / 10)) for i in range(20)]
        self._set_book([(round(p, 8), max(q, 0.01)) for p, q in bids], [(round(p, 8), max(q, 0.01)) for p, q in asks])
        for _ in range(r.randint(0, 4)):
            buy = r.random() < 0.5 + self.pressure / 2.5
            self._trade(now, ask if buy else bid, r.uniform(0.01, 0.5), not buy)
        t = int(now // 60 * 60_000)
        c = self.candles.items.get(t)
        if c is None:
            prev = self.candles.items.get(t - 60_000)
            if prev:
                prev.closed = True
            c = Candle(t, mid, mid, mid, mid, 0, False)
        c.high, c.low, c.close = max(c.high, mid), min(c.low, mid), mid
        self.candles.upsert(c)
