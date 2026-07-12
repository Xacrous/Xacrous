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


def test_compute_rejects_unknown_mode():
    with pytest.raises(ValueError):
        compute(_flat_df(200), mode="daytrade")


def test_compute_requires_at_least_50_candles_for_scalp():
    with pytest.raises(ValueError):
        compute(_flat_df(49), mode="scalp")


def test_compute_scalp_returns_expected_series():
    df = _flat_df(60)
    ind = compute(df, mode="scalp")
    assert ind.mode == "scalp"
    assert len(ind.ema9) == len(df)
    assert ind.latest(ind.ema9) == pytest.approx(100.0)
    assert ind.latest(ind.bb_mid) == pytest.approx(100.0)
    assert not np.isnan(ind.latest(ind.stoch_k))
    assert ind.volume_profile is not None
    assert len(ind.volume_profile.bins) > 0


def test_compute_scalp_vwap_tracks_price_in_uptrend():
    n = 60
    close = pd.Series(np.linspace(100, 110, n))
    df = pd.DataFrame({
        "open_time": np.arange(n) * 60_000 + 1_700_000_000_000,
        "open": close - 0.05,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": [50.0] * n,
    })
    ind = compute(df, mode="scalp")
    # VWAP is a volume-weighted average, so in a steady uptrend it should
    # sit below the latest close
    assert ind.latest(ind.vwap) < float(close.iloc[-1])


def test_compute_requires_at_least_50_candles_for_trade():
    with pytest.raises(ValueError):
        compute(_flat_df(49), mode="trade")


def test_compute_trade_returns_scalp_indicator_set_labeled_trade():
    df = _flat_df(60)
    ind = compute(df, mode="trade")
    assert ind.mode == "trade"
    assert ind.vwap is not None
    assert len(ind.atr14) == len(df)


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
