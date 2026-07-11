import pandas as pd
import pytest

from chartpilot.ta_engine.levels import (
    compute_pivot_points,
    fractal_swing_points,
    most_recent_swing_high,
    most_recent_swing_low,
)


def test_compute_pivot_points_formula():
    # last row is "in progress" and ignored; second-to-last is the reference period
    df = pd.DataFrame({
        "open": [0, 0],
        "high": [110.0, 0],
        "low": [90.0, 0],
        "close": [100.0, 0],
        "volume": [0, 0],
    })
    pivots = compute_pivot_points(df)
    assert pivots.pp == pytest.approx(100.0)
    assert pivots.r1 == pytest.approx(2 * 100 - 90)
    assert pivots.s1 == pytest.approx(2 * 100 - 110)
    assert pivots.r2 == pytest.approx(100 + (110 - 90))
    assert pivots.s2 == pytest.approx(100 - (110 - 90))


def test_compute_pivot_points_requires_two_rows():
    df = pd.DataFrame({"open": [0], "high": [1], "low": [1], "close": [1], "volume": [0]})
    with pytest.raises(ValueError):
        compute_pivot_points(df)


def test_nearest_level_finds_closest():
    df = pd.DataFrame({
        "open": [0, 0], "high": [110.0, 0], "low": [90.0, 0], "close": [100.0, 0], "volume": [0, 0],
    })
    pivots = compute_pivot_points(df)
    assert pivots.nearest_level(pivots.r1 + 0.01) == pytest.approx(pivots.r1)


def test_fractal_swing_points_detects_local_extremes():
    # a clean V shape: down then up, swing low at the bottom
    lows = [10, 9, 8, 7, 6, 7, 8, 9, 10]
    highs = [h + 2 for h in lows]
    df = pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": lows, "volume": [1] * len(lows)})
    swing_highs, swing_lows = fractal_swing_points(df, order=2)
    assert 4 in swing_lows  # index of the trough (value 6)


def test_most_recent_swing_low_and_high():
    lows = [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8]
    highs = [h + 2 for h in lows]
    df = pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": lows, "volume": [1] * len(lows)})
    assert most_recent_swing_low(df, order=2) == pytest.approx(6)


def test_most_recent_swing_low_returns_none_when_monotonic():
    lows = list(range(10))
    highs = [h + 2 for h in lows]
    df = pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": lows, "volume": [1] * len(lows)})
    assert most_recent_swing_low(df) is None
    assert most_recent_swing_high(df) is None
