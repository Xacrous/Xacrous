"""Exchange rules for one symbol: tick size, lot step and minimum order value."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


@dataclass(frozen=True)
class MarketInfo:
    symbol_id: str      # BTCUSDT
    ccxt_symbol: str    # BTC/USDT
    base: str
    quote: str
    tick: float         # price step
    step: float         # quantity step
    min_qty: float
    min_notional: float

    def _round(self, value: float, step: float, mode) -> float:
        if step <= 0:
            return value
        d, s = Decimal(str(value)), Decimal(str(step))
        return float((d / s).to_integral_value(rounding=mode) * s)

    def price_down(self, p: float) -> float:
        return self._round(p, self.tick, ROUND_FLOOR)

    def price_up(self, p: float) -> float:
        return self._round(p, self.tick, ROUND_CEILING)

    def qty_down(self, q: float) -> float:
        return self._round(q, self.step, ROUND_FLOOR)

    def sellable(self, qty: float, price: float) -> bool:
        q = self.qty_down(qty)
        return q >= self.min_qty and q * price >= self.min_notional

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def from_ccxt(exchange, symbol_id: str) -> MarketInfo:
    """Find a Binance spot market by its id (BTCUSDT) in loaded ccxt markets."""
    symbol_id = symbol_id.upper()
    m = exchange.markets_by_id.get(symbol_id)
    if isinstance(m, list):
        m = next((x for x in m if x.get("spot")), m[0] if m else None)
    if not m or not m.get("spot"):
        raise ValueError(f"{symbol_id} is not a Binance spot market")
    if not m.get("active", True):
        raise ValueError(f"{symbol_id} is not trading right now")
    prec, limits = m["precision"], m.get("limits", {})
    tick_size_mode = getattr(exchange, "precisionMode", 4) == 4  # ccxt TICK_SIZE
    tick = prec["price"] if tick_size_mode else 10 ** -prec["price"]
    step = prec["amount"] if tick_size_mode else 10 ** -prec["amount"]
    return MarketInfo(symbol_id, m["symbol"], m["base"], m["quote"], float(tick), float(step),
                      float((limits.get("amount") or {}).get("min") or 0),
                      float((limits.get("cost") or {}).get("min") or 0))
