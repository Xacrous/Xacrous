"""Order book snapshot and the metrics the strategy reads from it."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Book:
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, qty), best (highest) first
    asks: list[tuple[float, float]] = field(default_factory=list)  # (price, qty), best (lowest) first
    ts: float = 0.0                                                 # local receive time, seconds

    @property
    def ready(self) -> bool:
        return bool(self.bids and self.asks)

    @property
    def best_bid(self) -> float:
        return self.bids[0][0]

    @property
    def best_ask(self) -> float:
        return self.asks[0][0]

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        return self.spread / self.mid * 10_000

    @property
    def microprice(self) -> float:
        """Mid weighted by the opposite side's size: leans toward the side likely to be hit next."""
        (b, bq), (a, aq) = self.bids[0], self.asks[0]
        return (a * bq + b * aq) / (bq + aq) if bq + aq else self.mid

    def micro_skew(self) -> float:
        """Microprice position inside the spread: -1 (at bid, selling pressure) .. +1 (at ask, buying pressure)."""
        half = self.spread / 2
        if half <= 0:
            return 0.0
        return max(-1.0, min(1.0, (self.microprice - self.mid) / half))

    def depth(self, depth_bps: float) -> tuple[float, float]:
        """Distance-weighted quote volume on each side within ``depth_bps`` of mid.

        Levels at the touch count fully and the weight falls linearly to zero at the
        edge of the window, so far-away (often fake) walls matter less.
        """
        mid, window = self.mid, depth_bps / 10_000

        def side(levels):
            total = 0.0
            for price, qty in levels:
                dist = abs(price - mid) / mid
                if dist > window:
                    break
                total += price * qty * (1 - dist / window if window else 1)
            return total

        return side(self.bids), side(self.asks)

    def imbalance(self, depth_bps: float) -> float:
        """(bid volume - ask volume) / total within the window: -1 .. +1."""
        b, a = self.depth(depth_bps)
        return (b - a) / (b + a) if b + a else 0.0

    def market_sell_price(self, qty: float) -> float:
        """Average price a market sell of ``qty`` would get by walking the bids."""
        left, value = qty, 0.0
        for price, size in self.bids:
            take = min(left, size)
            value += take * price
            left -= take
            if left <= 0:
                return value / qty
        return value / (qty - left) if qty > left else (self.bids[-1][0] if self.bids else 0.0)
