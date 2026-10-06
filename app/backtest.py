"""Backtest of the trend-band strategy on daily candles.

Signals use the candle close; fills happen at the next candle's open, with a
fee charged on both the buy and the sell. Equity is marked to market daily
(open to open) so drawdowns include losses while a trade is still open.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .strategy import StrategyParams, sma


@dataclass
class Candle:
    ts: int        # open time, ms since epoch (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def run_backtest(candles: list[Candle], params: StrategyParams = StrategyParams(),
                 fee: float = 0.001, start_equity: float = 10_000.0) -> dict:
    n = len(candles)
    closes = [c.close for c in candles]
    avg = sma(closes, params.sma_length)
    start = params.sma_length - 1
    if n - start < 2:
        raise ValueError("not enough candles for a backtest")

    equity = start_equity
    in_pos = False
    entry_px = entry_ts = None
    trades: list[dict] = []
    curve: list[dict] = []
    for i in range(start, n - 1):
        if in_pos:  # held from open i to open i+1
            equity *= candles[i + 1].open / candles[i].open
        m = avg[i]
        if not in_pos and closes[i] > m * (1 + params.entry_band):
            in_pos, entry_px, entry_ts = True, candles[i + 1].open, candles[i + 1].ts
            equity *= 1 - fee
        elif in_pos and closes[i] < m * (1 - params.exit_band):
            exit_px = candles[i + 1].open
            trades.append(_trade(entry_ts, entry_px, candles[i + 1].ts, exit_px, fee, open_=False))
            in_pos = False
            equity *= 1 - fee
        curve.append({"ts": candles[i + 1].ts, "equity": equity})

    if in_pos:  # mark the open trade at the last close
        last = candles[-1]
        equity *= last.close / last.open
        curve[-1] = {"ts": last.ts, "equity": equity}
        trades.append(_trade(entry_ts, entry_px, last.ts, last.close, fee, open_=True))

    days = (candles[-1].ts - candles[start + 1].ts) / 86_400_000 or 1
    bh_mult = candles[-1].close / candles[start + 1].open
    bh_curve = [c.open for c in candles[start + 1:]] + [candles[-1].close]
    closed = [t for t in trades if not t["open"]]
    wins = [t for t in closed if t["return_pct"] > 0]
    gross_win = sum(t["return_pct"] for t in wins)
    gross_loss = -sum(t["return_pct"] for t in closed if t["return_pct"] <= 0)
    mult = equity / start_equity
    return {
        "params": params.to_dict(),
        "fee": fee,
        "start": candles[start + 1].ts,
        "end": candles[-1].ts,
        "stats": {
            "trades": len(closed),
            "open_trade": bool(trades and trades[-1]["open"]),
            "win_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
            "total_return_pct": round((mult - 1) * 100, 1),
            "cagr_pct": round((mult ** (365 / days) - 1) * 100, 1),
            "max_drawdown_pct": round(_max_dd([p["equity"] for p in curve]) * 100, 1),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "time_in_market_pct": round(100 * _days_in_market(trades) / days, 1),
            "buy_hold_return_pct": round((bh_mult - 1) * 100, 1),
            "buy_hold_cagr_pct": round((bh_mult ** (365 / days) - 1) * 100, 1),
            "buy_hold_max_drawdown_pct": round(_max_dd(bh_curve) * 100, 1),
            "final_equity": round(equity, 2),
        },
        "trades": trades,
        "equity_curve": curve,
    }


def _trade(entry_ts, entry_px, exit_ts, exit_px, fee, open_):
    ret = exit_px / entry_px * (1 - fee) ** 2 - 1
    return {"entry_ts": entry_ts, "entry_price": entry_px, "exit_ts": exit_ts,
            "exit_price": exit_px, "return_pct": round(ret * 100, 2), "open": open_}


def _max_dd(values):
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def _days_in_market(trades):
    return sum((t["exit_ts"] - t["entry_ts"]) / 86_400_000 for t in trades)
