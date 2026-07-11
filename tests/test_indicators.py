import numpy as np
import pandas as pd
import pytest

from chartpilot.ta_engine.indicators import compute


def _flat_df(n=210, price=100.0):
    return pd.DataFrame({
        "open_time": np.arange(n) * 3600_000,
        "open": [price] * n,
        "high": [price + 1] * n,
        "low": [price - 1] * n,
        "close": [price] * n,
        "volume": [50.0] * n,
    })


def test_compute_requires_at_least_200_candles():
    with pytest.raises(ValueError):
        compute(_flat_df(199), mode="swing")


def test_compute_rejects_unimplemented_mode():
    with pytest.raises(NotImplementedError):
        compute(_flat_df(200), mode="scalp")


def test_compute_swing_returns_expected_series():
    df = _flat_df(210)
    ind = compute(df, mode="swing")
    assert ind.mode == "swing"
    assert len(ind.sma20) == len(df)
    assert len(ind.sma200) == len(df)
    # constant price -> SMA converges to price, RSI is undefined/neutral-ish but not NaN at the end
    assert ind.latest(ind.sma20) == pytest.approx(100.0)
    assert ind.latest(ind.sma200) == pytest.approx(100.0)
    assert not np.isnan(ind.latest(ind.atr14))


def test_compute_swing_uptrend_sma_ordering():
    n = 210
    close = pd.Series(np.linspace(100, 160, n))
    df = pd.DataFrame({
        "open_time": np.arange(n) * 3600_000,
        "open": close - 0.1,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": [50.0] * n,
    })
    ind = compute(df, mode="swing")
    # in a clean uptrend, price > sma20 > sma50 > sma200
    assert ind.latest(ind.sma20) > ind.latest(ind.sma50) > ind.latest(ind.sma200)
    assert float(close.iloc[-1]) > ind.latest(ind.sma20)
