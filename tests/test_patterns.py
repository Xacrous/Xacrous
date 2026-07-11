import numpy as np
import pandas as pd
import pytest

from chartpilot.ta_engine.patterns import detect_fibonacci_retracement, detect_range_breakout


def test_range_breakout_detects_long_breakout_with_volume():
    close = pd.Series([100.0] * 20 + [100, 100.5, 101, 103, 106])
    high, low = close + 0.3, close - 0.3
    volume = pd.Series([50.0] * 24 + [120.0])
    df = pd.DataFrame({"open": close - 0.1, "high": high, "low": low, "close": close, "volume": volume})
    result = detect_range_breakout(df, lookback=20, volume_multiplier=1.5)
    assert result is not None
    assert result.direction == "long"
    assert result.volume_ratio >= 1.5
    assert result.measured_move_target() == pytest.approx(result.breakout_price + result.range_height)


def test_range_breakout_none_without_volume_confirmation():
    close = pd.Series([100.0] * 20 + [100, 100.5, 101, 103, 106])
    high, low = close + 0.3, close - 0.3
    volume = pd.Series([50.0] * 25)  # no spike
    df = pd.DataFrame({"open": close - 0.1, "high": high, "low": low, "close": close, "volume": volume})
    assert detect_range_breakout(df, lookback=20, volume_multiplier=1.5) is None


def test_range_breakout_none_when_price_stays_inside_range():
    close = pd.Series([100.0] * 25)
    high, low = close + 0.3, close - 0.3
    volume = pd.Series([50.0] * 24 + [120.0])
    df = pd.DataFrame({"open": close - 0.1, "high": high, "low": low, "close": close, "volume": volume})
    assert detect_range_breakout(df, lookback=20, volume_multiplier=1.5) is None


def test_range_breakout_short_direction():
    close = pd.Series([100.0] * 20 + [100, 99.5, 99, 97, 94])
    high, low = close + 0.3, close - 0.3
    volume = pd.Series([50.0] * 24 + [120.0])
    df = pd.DataFrame({"open": close - 0.1, "high": high, "low": low, "close": close, "volume": volume})
    result = detect_range_breakout(df, lookback=20, volume_multiplier=1.5)
    assert result is not None
    assert result.direction == "short"


def _impulse_fixture():
    lows = [10] * 5 + list(np.linspace(10, 5, 10))[:-1] + list(np.linspace(5, 20, 20)) + [19] * 26
    highs = [l + 1 for l in lows]
    close = pd.Series([(h + l) / 2 for h, l in zip(highs, lows)])
    df = pd.DataFrame({"open": close, "high": highs, "low": lows, "close": close, "volume": [10] * len(close)})
    atr = pd.Series([1.0] * len(close))
    return df, atr


def test_fibonacci_retracement_detects_impulse_and_levels():
    df, atr = _impulse_fixture()
    fib = detect_fibonacci_retracement(df, atr, lookback=60, min_atr_multiple=3.0)
    assert fib is not None
    assert fib.direction == "long"
    assert fib.swing_start == pytest.approx(5.0)
    assert fib.swing_end == pytest.approx(21.0)
    assert fib.levels[0.5] == pytest.approx((5.0 + 21.0) / 2)
    lo, hi = fib.zone()
    assert lo < hi


def test_fibonacci_retracement_none_when_move_too_small():
    df, atr = _impulse_fixture()
    atr = pd.Series([50.0] * len(df))  # inflate ATR so the move no longer qualifies as impulsive
    assert detect_fibonacci_retracement(df, atr, lookback=60, min_atr_multiple=3.0) is None


def test_fibonacci_retracement_none_with_insufficient_history():
    df, atr = _impulse_fixture()
    assert detect_fibonacci_retracement(df.iloc[:10], atr.iloc[:10], lookback=60) is None
