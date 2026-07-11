import pandas as pd
import pytest

from chartpilot.signal_engine.base_strategy import Signal
from chartpilot.signal_engine.signal_log import SignalLog

_GENERATED_AT_MS = 1_767_225_600_000  # 2026-01-01T00:00:00+00:00


def _signal(**overrides):
    defaults = dict(
        symbol="BTCUSDT", timeframe="4h", mode="swing", strategy="trend_following_ma_cross",
        direction="long", entry=100.0, take_profit=110.0, stop_loss=95.0, confidence=78,
        reward_risk_ratio=2.0, rationale=["test"], generated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return Signal(**defaults)


@pytest.fixture
def log(tmp_path):
    l = SignalLog(tmp_path / "signal_log.sqlite3")
    yield l
    l.close()


def _candles_after(ms_offset_hours, values):
    return pd.DataFrame({
        "open_time": [_GENERATED_AT_MS + h * 3600_000 for h in ms_offset_hours],
        "open": [v[0] for v in values], "high": [v[1] for v in values],
        "low": [v[2] for v in values], "close": [v[3] for v in values], "volume": [10.0] * len(values),
    })


def test_record_if_new_inserts_and_dedupes_pending(log):
    sig = _signal()
    row_id = log.record_if_new(sig)
    assert row_id is not None
    duplicate_id = log.record_if_new(sig)
    assert duplicate_id is None
    assert len(log.list_recent(10)) == 1


def test_record_if_new_allows_new_row_once_prior_is_resolved(log):
    sig = _signal()
    log.record_if_new(sig)
    df = _candles_after([1, 2], [[101, 102, 100, 101.5], [102, 112, 101, 111]])
    resolved = log.resolve_pending("BTCUSDT", "4h", df)
    assert resolved == 1
    row_id = log.record_if_new(sig)
    assert row_id is not None  # prior signal resolved, so a fresh one can be logged


def test_resolve_pending_marks_hit_tp(log):
    log.record_if_new(_signal())
    df = _candles_after([1, 2], [[101, 102, 100, 101.5], [102, 112, 101, 111]])
    resolved = log.resolve_pending("BTCUSDT", "4h", df)
    assert resolved == 1
    row = log.list_recent(1)[0]
    assert row["status"] == "hit_tp"
    assert row["resolved_at"] is not None


def test_resolve_pending_marks_hit_sl(log):
    log.record_if_new(_signal())
    df = _candles_after([1, 2], [[101, 102, 100, 101.5], [99, 100, 93, 94]])
    resolved = log.resolve_pending("BTCUSDT", "4h", df)
    assert resolved == 1
    assert log.list_recent(1)[0]["status"] == "hit_sl"


def test_resolve_pending_leaves_unresolved_signals_pending(log):
    log.record_if_new(_signal())
    df = _candles_after([1], [[101, 102, 100, 101.5]])  # neither TP nor SL touched
    resolved = log.resolve_pending("BTCUSDT", "4h", df)
    assert resolved == 0
    assert log.list_recent(1)[0]["status"] == "pending"


def test_resolve_pending_marks_expired(log):
    log.record_if_new(_signal(expires_at="2026-01-01T01:00:00+00:00"))
    df = _candles_after([2], [[101, 102, 100, 101.5]])  # after expiry, no TP/SL hit
    resolved = log.resolve_pending("BTCUSDT", "4h", df)
    assert resolved == 1
    assert log.list_recent(1)[0]["status"] == "expired"


def test_resolve_pending_scoped_to_symbol_and_timeframe(log):
    log.record_if_new(_signal())
    df = _candles_after([1, 2], [[101, 102, 100, 101.5], [102, 112, 101, 111]])
    resolved = log.resolve_pending("ETHUSDT", "4h", df)  # different symbol
    assert resolved == 0
    assert log.list_recent(1)[0]["status"] == "pending"


def test_win_rate_none_without_resolved_trades(log):
    log.record_if_new(_signal())
    assert log.win_rate("trend_following_ma_cross") is None


def test_win_rate_computes_hit_rate(log):
    log.record_if_new(_signal(confidence=80))
    df = _candles_after([1, 2], [[101, 102, 100, 101.5], [102, 112, 101, 111]])
    log.resolve_pending("BTCUSDT", "4h", df)
    assert log.win_rate("trend_following_ma_cross") == (1.0, 1)


def test_win_rate_filters_by_confidence_band(log):
    log.record_if_new(_signal(confidence=80))
    df = _candles_after([1, 2], [[101, 102, 100, 101.5], [102, 112, 101, 111]])
    log.resolve_pending("BTCUSDT", "4h", df)
    assert log.win_rate("trend_following_ma_cross", confidence=20, band=5) is None


def test_export_csv_writes_header_and_rows(log, tmp_path):
    log.record_if_new(_signal())
    out = tmp_path / "export.csv"
    log.export_csv(out)
    content = out.read_text()
    assert "symbol,timeframe,mode,strategy" in content
    assert "BTCUSDT" in content
