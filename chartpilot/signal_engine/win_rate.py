"""Blends the historical backtester's replay stats with the forward
signal log's live track record into the single `historical_win_rate`
figure shown on a Signal (Section 3's confidence-vs-win-rate distinction).

The forward log is weighted more heavily as it accumulates real samples —
it's graded on ChartPilot's own live calls, not just curve-fit history —
but a fresh symbol/strategy pairing with no live history yet still gets a
useful number from the backtest alone.
"""

from __future__ import annotations

_FULL_FORWARD_WEIGHT_SAMPLE_COUNT = 10


def blend_win_rate(
    backtest_stats: tuple[float, int] | None,
    forward_stats: tuple[float, int] | None,
) -> float | None:
    if backtest_stats is None and forward_stats is None:
        return None
    if forward_stats is None:
        return round(backtest_stats[0], 4)
    if backtest_stats is None:
        return round(forward_stats[0], 4)

    backtest_rate, _ = backtest_stats
    forward_rate, forward_n = forward_stats
    forward_weight = min(1.0, forward_n / _FULL_FORWARD_WEIGHT_SAMPLE_COUNT)
    blended = forward_weight * forward_rate + (1 - forward_weight) * backtest_rate
    return round(blended, 4)
