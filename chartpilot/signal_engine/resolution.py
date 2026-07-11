"""Shared TP/SL/expiry resolution logic, used by both the historical
backtester (replaying candles that already happened) and the forward
signal log (resolving live signals as new candles arrive) — so a signal is
graded identically whether it's being replayed or lived through.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

Outcome = Literal["hit_tp", "hit_sl", "expired", "pending"]


def resolve_signal(
    direction: str,
    take_profit: float,
    stop_loss: float,
    expires_at_ms: int | None,
    candles: pd.DataFrame,
) -> tuple[Outcome, int | None]:
    """Walk `candles` (strictly after the signal's generation, oldest to
    newest) and return the first outcome reached.

    If a single candle's range touches both TP and SL, the stop is
    conservatively assumed to have been hit first — the standard
    pessimistic tie-break for backtesting without intra-candle tick data.
    """
    bullish = direction == "long"
    for row in candles.itertuples(index=False):
        open_time = int(row.open_time)
        if expires_at_ms is not None and open_time > expires_at_ms:
            return "expired", open_time
        hit_tp = row.high >= take_profit if bullish else row.low <= take_profit
        hit_sl = row.low <= stop_loss if bullish else row.high >= stop_loss
        if hit_sl:
            return "hit_sl", open_time
        if hit_tp:
            return "hit_tp", open_time
    return "pending", None
