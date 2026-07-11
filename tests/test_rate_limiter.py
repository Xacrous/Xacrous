import time

import pytest

from chartpilot.data_fetcher.rate_limiter import TokenBucketRateLimiter


def test_acquire_spends_tokens_without_blocking_when_available():
    limiter = TokenBucketRateLimiter(capacity=10, refill_rate=1)
    start = time.monotonic()
    limiter.acquire(weight=5)
    assert time.monotonic() - start < 0.1


def test_acquire_blocks_until_refill():
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=20)  # 0.05s per token
    limiter.acquire(weight=1)
    start = time.monotonic()
    limiter.acquire(weight=1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.03


def test_weight_exceeding_capacity_raises():
    limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1)
    with pytest.raises(ValueError):
        limiter.acquire(weight=10)


def test_on_rate_limited_backs_off_subsequent_acquire():
    limiter = TokenBucketRateLimiter(capacity=10, refill_rate=100)
    delay = limiter.on_rate_limited(attempt=0, base_delay=0.05, max_delay=1.0)
    assert delay == pytest.approx(0.05, abs=0.01)
    start = time.monotonic()
    limiter.acquire(weight=1)
    assert time.monotonic() - start >= 0.03


def test_on_rate_limited_exponential_growth_capped():
    limiter = TokenBucketRateLimiter(capacity=10, refill_rate=100)
    delay_small = limiter.on_rate_limited(attempt=0, base_delay=1.0, max_delay=5.0)
    delay_large = limiter.on_rate_limited(attempt=10, base_delay=1.0, max_delay=5.0)
    assert delay_small < delay_large
    assert delay_large == 5.0


def test_invalid_construction_raises():
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(capacity=0, refill_rate=1)
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(capacity=1, refill_rate=0)
