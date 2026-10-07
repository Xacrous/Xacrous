"""Turns order book + trade flow + 1m candles into a long-entry decision.

Score (-1 .. +1) is a weighted mix of three short-term pressure readings:
  * imbalance  - resting bid vs ask volume near the mid price
  * micro      - where the size-weighted microprice sits inside the spread
  * flow       - aggressive buy vs sell volume in recent trades
A buy is only allowed when the score stays above the entry level for a few
seconds AND the market filters pass (tight spread, enough 1m volatility to
reach the profit target, optional 1m uptrend).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .book import Book
from .flow import Candles, TradeFlow


@dataclass
class SignalParams:
    depth_bps: float = 10.0          # look at book levels within 0.10% of mid
    flow_window_s: float = 30.0      # trade flow window
    w_imbalance: float = 0.4
    w_micro: float = 0.2
    w_flow: float = 0.4
    entry_score: float = 0.35        # buy when score >= this ...
    confirm_s: float = 2.0           # ... continuously for this long
    exit_score: float = -0.40        # bail out of a trade below this ...
    exit_confirm_s: float = 3.0      # ... once it has stayed there this long (ignores one-tick blips)
    max_spread_bps: float = 5.0      # skip wide, illiquid books
    min_trades: int = 10             # need at least this many trades in the flow window
    vol_bars: int = 15               # average 1m range over this many candles ...
    min_vol_mult: float = 1.5        # ... must be >= this x the take-profit distance
    trend_filter: bool = True        # only buy while the 1m close is above its EMA
    trend_ema: int = 20


@dataclass
class Snapshot:
    score: float = 0.0
    imbalance: float = 0.0
    micro: float = 0.0
    flow: float = 0.0
    trades: int = 0
    spread_bps: float = 0.0
    avg_range_pct: float | None = None
    ema: float | None = None
    spread_ok: bool = False
    flow_ok: bool = False
    vol_ok: bool = False
    trend_ok: bool = False
    score_ok: bool = False
    confirmed_s: float = 0.0
    buy: bool = False
    blockers: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def score(book: Book, flow: TradeFlow, p: SignalParams, now: float) -> Snapshot:
    snap = Snapshot()
    if not book.ready:
        snap.blockers = ["waiting for order book"]
        return snap
    snap.imbalance = book.imbalance(p.depth_bps)
    snap.micro = book.micro_skew()
    f = flow.stats(p.flow_window_s, now)
    snap.flow, snap.trades = f["flow"], f["count"]
    w = p.w_imbalance + p.w_micro + p.w_flow
    snap.score = (p.w_imbalance * snap.imbalance + p.w_micro * snap.micro + p.w_flow * snap.flow) / w
    snap.spread_bps = book.spread_bps
    return snap


class EntrySignal:
    """Applies filters and the confirmation timer on top of the raw score."""

    def __init__(self, params: SignalParams):
        self.p = params
        self._above_since: float | None = None

    def reset(self) -> None:
        self._above_since = None

    def evaluate(self, book: Book, flow: TradeFlow, candles: Candles, target_gross_pct: float,
                 now: float) -> Snapshot:
        p = self.p
        snap = score(book, flow, p, now)
        if not book.ready:
            self.reset()
            return snap
        blockers = []
        if now - book.ts > 3.0:
            blockers.append("order book data is stale")
        snap.spread_ok = snap.spread_bps <= p.max_spread_bps
        if not snap.spread_ok:
            blockers.append(f"spread {snap.spread_bps:.1f} bps > {p.max_spread_bps:g}")
        snap.flow_ok = snap.trades >= p.min_trades
        if not snap.flow_ok:
            blockers.append(f"only {snap.trades} trades in {p.flow_window_s:g}s")
        snap.avg_range_pct = candles.avg_range_pct(p.vol_bars)
        need = target_gross_pct * p.min_vol_mult
        snap.vol_ok = snap.avg_range_pct is not None and snap.avg_range_pct >= need
        if snap.avg_range_pct is None:
            blockers.append(f"need {p.vol_bars} closed 1m candles")
        elif not snap.vol_ok:
            blockers.append(f"1m range {snap.avg_range_pct:.3f}% < {need:.3f}% needed")
        if p.trend_filter:
            snap.ema = candles.ema(p.trend_ema)
            snap.trend_ok = snap.ema is not None and book.mid > snap.ema
            if not snap.trend_ok:
                blockers.append("price below 1m EMA" if snap.ema else f"need {p.trend_ema} 1m candles for EMA")
        else:
            snap.trend_ok = True
        snap.score_ok = snap.score >= p.entry_score
        if snap.score_ok:
            if self._above_since is None:
                self._above_since = now
            snap.confirmed_s = now - self._above_since
        else:
            self._above_since = None
            blockers.append(f"score {snap.score:+.2f} < {p.entry_score:+.2f}")
        if snap.score_ok and snap.confirmed_s < p.confirm_s:
            blockers.append(f"confirming {snap.confirmed_s:.1f}/{p.confirm_s:g}s")
        snap.buy = not blockers
        snap.blockers = blockers
        return snap
