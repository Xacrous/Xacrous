import pytest

from chartpilot.signal_engine.win_rate import blend_win_rate


def test_both_none_returns_none():
    assert blend_win_rate(None, None) is None


def test_only_backtest_returns_backtest_rate():
    assert blend_win_rate((0.65, 20), None) == pytest.approx(0.65)


def test_only_forward_returns_forward_rate():
    assert blend_win_rate(None, (0.8, 3)) == pytest.approx(0.8)


def test_blend_weights_forward_log_by_sample_count():
    # forward_n=5 -> forward weight 0.5
    blended = blend_win_rate(backtest_stats=(0.4, 100), forward_stats=(0.8, 5))
    assert blended == pytest.approx(0.5 * 0.8 + 0.5 * 0.4)


def test_blend_caps_forward_weight_at_full_sample_count():
    # forward_n >= 10 -> forward weight fully 1.0, backtest ignored
    blended = blend_win_rate(backtest_stats=(0.1, 100), forward_stats=(0.9, 50))
    assert blended == pytest.approx(0.9)


def test_blend_with_zero_forward_samples_falls_back_to_backtest():
    blended = blend_win_rate(backtest_stats=(0.6, 10), forward_stats=(1.0, 0))
    assert blended == pytest.approx(0.6)
