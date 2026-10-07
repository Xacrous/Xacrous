"""Recent trades (who is aggressive) and 1-minute candles (how much price moves)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


class TradeFlow:
    """Rolling window of public trades."""

    def __init__(self, keep_s: float = 300):
        self.keep_s = keep_s
        self.trades: deque[tuple[float, float, float, bool]] = deque()  # (ts, price, qty, buyer_is_maker)

    def add(self, ts: float, price: float, qty: float, buyer_is_maker: bool) -> None:
        self.trades.append((ts, price, qty, buyer_is_maker))
        while self.trades and ts - self.trades[0][0] > self.keep_s:
            self.trades.popleft()

    def stats(self, window_s: float, now: float) -> dict:
        buy = sell = 0.0
        n = 0
        for ts, price, qty, buyer_is_maker in reversed(self.trades):
            if now - ts > window_s:
                break
            n += 1
            if buyer_is_maker:   # the seller crossed the spread: aggressive sell
                sell += price * qty
            else:                # the buyer crossed the spread: aggressive buy
                buy += price * qty
        total = buy + sell
        return {"buy_quote": buy, "sell_quote": sell, "count": n,
                "flow": (buy - sell) / total if total else 0.0}

    @property
    def last_price(self) -> float | None:
        return self.trades[-1][1] if self.trades else None


@dataclass
class Candle:
    open_time: int   # ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool


class Candles:
    """1-minute candles, newest last."""

    def __init__(self, keep: int = 200):
        self.keep = keep
        self.items: dict[int, Candle] = {}

    def upsert(self, c: Candle) -> None:
        self.items[c.open_time] = c
        if len(self.items) > self.keep:
            for k in sorted(self.items)[: len(self.items) - self.keep]:
                del self.items[k]

    def closed(self) -> list[Candle]:
        return [self.items[k] for k in sorted(self.items) if self.items[k].closed]

    def avg_range_pct(self, n: int) -> float | None:
        """Average (high - low) / open of the last ``n`` closed candles, in percent."""
        bars = self.closed()[-n:]
        if len(bars) < n:
            return None
        return sum((b.high - b.low) / b.open for b in bars) / n * 100

    def ema(self, n: int) -> float | None:
        closes = [b.close for b in self.closed()]
        if len(closes) < n:
            return None
        k = 2 / (n + 1)
        value = sum(closes[:n]) / n
        for c in closes[n:]:
            value = c * k + value * (1 - k)
        return value

    def last_close(self) -> float | None:
        bars = self.closed()
        return bars[-1].close if bars else None
