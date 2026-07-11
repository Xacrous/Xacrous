import pytest

from chartpilot.signal_engine.scorer import ScoreFactor, rationale_lines, total_confidence


def test_score_factor_rejects_out_of_range():
    with pytest.raises(ValueError):
        ScoreFactor("bad", score=31, max_score=30)
    with pytest.raises(ValueError):
        ScoreFactor("bad", score=-1, max_score=30)


def test_total_confidence_sums_and_rounds():
    factors = [
        ScoreFactor("Trend", 25.4, 30),
        ScoreFactor("Momentum", 20.2, 25),
    ]
    assert total_confidence(factors) == round(25.4 + 20.2)


def test_total_confidence_clamps_to_0_100():
    factors = [ScoreFactor("Trend", 30, 30), ScoreFactor("Momentum", 25, 25),
               ScoreFactor("Location", 25, 25), ScoreFactor("Volume", 20, 20)]
    assert total_confidence(factors) == 100


def test_rationale_lines_formats_description_and_fraction():
    factors = [ScoreFactor("Trend: price above SMA50", 25, 30)]
    lines = rationale_lines(factors)
    assert lines == ["Trend: price above SMA50 (25/30)"]
