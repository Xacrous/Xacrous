"""Client-side token-bucket rate limiter with exponential backoff.

Binance enforces IP-based, weight-based limits on spot endpoints rather than
a fixed request count. Rather than hard-coding the current cap (Binance
adjusts it from time to time), callers size the bucket comfortably under
whatever `/api/v3/exchangeInfo` reports at runtime and let this limiter
throttle locally; `on_rate_limited` handles the HTTP 429/418 backoff path.
"""

from __future__ import annotations

import threading
import time


class TokenBucketRateLimiter:
    """A simple thread-safe token bucket.

    `capacity` tokens refill continuously at `refill_rate` tokens/second.
    `acquire(weight)` blocks until enough tokens are available, then spends
    them — this is what keeps ChartPilot's request volume comfortably under
    Binance's reported weight cap regardless of the exact numbers in force
    at any given time.
    """

    def __init__(self, capacity: float, refill_rate: float) -> None:
        if capacity <= 0 or refill_rate <= 0:
            raise ValueError("capacity and refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._backoff_until = 0.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last_refill = now

    def acquire(self, weight: float = 1.0) -> None:
        """Block until `weight` tokens are available, then spend them."""
        if weight > self.capacity:
            raise ValueError(f"weight {weight} exceeds bucket capacity {self.capacity}")
        while True:
            with self._lock:
                now = time.monotonic()
                wait_for_backoff = max(0.0, self._backoff_until - now)
                if wait_for_backoff <= 0:
                    self._refill()
                    if self._tokens >= weight:
                        self._tokens -= weight
                        return
                    shortfall = weight - self._tokens
                    sleep_for = shortfall / self.refill_rate
                else:
                    sleep_for = wait_for_backoff
            time.sleep(sleep_for)

    def on_rate_limited(self, attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
        """Register an HTTP 429/418 response and compute the backoff delay.

        Exponential backoff: base_delay * 2**attempt, capped at max_delay.
        Also blocks new `acquire()` calls until the backoff window elapses,
        so a single 429 pauses the whole client rather than just the caller
        that triggered it.
        """
        delay = min(max_delay, base_delay * (2 ** attempt))
        with self._lock:
            self._backoff_until = max(self._backoff_until, time.monotonic() + delay)
        return delay
