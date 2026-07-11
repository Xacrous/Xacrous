import pandas as pd
import pytest

from chartpilot.signal_engine.resolution import resolve_signal


def _candles(rows):
    return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])


def test_long_hits_take_profit():
    candles = _candles([
        [1000, 100, 101, 99, 100.5, 10],
        [2000, 101, 112, 100, 111, 10],
    ])
    outcome, resolved_at = resolve_signal("long", take_profit=110, stop_loss=95, expires_at_ms=None, candles=candles)
    assert outcome == "hit_tp"
    assert resolved_at == 2000


def test_long_hits_stop_loss():
    candles = _candles([
        [1000, 100, 101, 99, 100.5, 10],
        [2000, 99, 100, 93, 94, 10],
    ])
    outcome, resolved_at = resolve_signal("long", take_profit=110, stop_loss=95, expires_at_ms=None, candles=candles)
    assert outcome == "hit_sl"
    assert resolved_at == 2000


def test_ambiguous_candle_conservatively_resolves_to_stop_loss():
    candles = _candles([[1000, 100, 115, 90, 105, 10]])  # touches both TP(110) and SL(95)
    outcome, _ = resolve_signal("long", take_profit=110, stop_loss=95, expires_at_ms=None, candles=candles)
    assert outcome == "hit_sl"


def test_short_direction_mirrors_long():
    candles = _candles([[1000, 100, 101, 88, 90, 10]])  # low <= TP(90) for a short
    outcome, _ = resolve_signal("short", take_profit=90, stop_loss=110, expires_at_ms=None, candles=candles)
    assert outcome == "hit_tp"


def test_expires_before_either_level_hit():
    candles = _candles([
        [1000, 100, 101, 99, 100.5, 10],
        [5000, 101, 105, 100, 104, 10],  # open_time beyond expiry, neither TP(110) nor SL(90) touched
    ])
    outcome, resolved_at = resolve_signal("long", take_profit=110, stop_loss=90, expires_at_ms=2000, candles=candles)
    assert outcome == "expired"
    assert resolved_at == 5000


def test_pending_when_neither_hit_nor_expired():
    candles = _candles([[1000, 100, 101, 99, 100.5, 10]])
    outcome, resolved_at = resolve_signal("long", take_profit=110, stop_loss=90, expires_at_ms=None, candles=candles)
    assert outcome == "pending"
    assert resolved_at is None


def test_pending_when_no_candles():
    candles = _candles([])
    outcome, resolved_at = resolve_signal("long", take_profit=110, stop_loss=90, expires_at_ms=None, candles=candles)
    assert outcome == "pending"
    assert resolved_at is None
