"""The trading loop: once per closed daily candle, evaluate the strategy and trade."""
from __future__ import annotations

import os
import threading
import time

from .backtest import Candle
from .broker import OrderError
from .config import Settings
from .data import DAY_MS, closed_only, fetch_exchange, load_file
from .store import Store
from .strategy import evaluate

MIN_POSITION_VALUE = 5.0  # below this (in quote currency) a leftover balance counts as flat


class FileData:
    """Candles from a local file. Every row is treated as a closed candle."""

    def __init__(self, path: str):
        self.path = path
        self._cache: tuple[float, list[Candle]] | None = None

    def history(self) -> list[Candle]:
        mtime = os.path.getmtime(self.path)
        if not self._cache or self._cache[0] != mtime:
            self._cache = (mtime, load_file(self.path))
        return self._cache[1]

    def closed(self) -> list[Candle]:
        return self.history()

    def price(self) -> float:
        return self.history()[-1].close


class ExchangeData:
    """Daily candles and ticker from the exchange, cached in memory."""

    HISTORY_START_MS = 1_502_928_000_000  # 2017-08-17, first BTC/USDT day on Binance

    def __init__(self, exchange, symbol: str):
        self.ex, self.symbol = exchange, symbol
        self._candles: dict[int, Candle] = {}
        self._lock = threading.Lock()

    def history(self) -> list[Candle]:
        with self._lock:
            if not self._candles:
                fresh = fetch_exchange(self.ex, self.symbol, since_ms=self.HISTORY_START_MS)
            else:
                fresh = fetch_exchange(self.ex, self.symbol, since_ms=max(self._candles) - 5 * DAY_MS)
            for c in fresh:
                self._candles[c.ts] = c
            return [self._candles[k] for k in sorted(self._candles)]

    def closed(self) -> list[Candle]:
        return closed_only(self.history())

    def price(self) -> float:
        return float(self.ex.fetch_ticker(self.symbol)["last"])


class Engine:
    def __init__(self, settings: Settings, store: Store, broker, market):
        self.s, self.store, self.broker, self.market = settings, store, broker, market
        self._lock = threading.Lock()

    # ----- state -------------------------------------------------------------
    @property
    def paused(self) -> bool:
        return bool(self.store.get("paused", False))

    def set_paused(self, value: bool) -> None:
        self.store.set("paused", value)
        self.store.log("Trading paused" if value else "Trading resumed", "warn" if value else "info")

    def position_qty(self) -> float:
        return float(self.store.get("position_qty", 0.0))

    # ----- main step ---------------------------------------------------------
    def run_once(self) -> dict:
        """Evaluate the last closed candle and trade if the signal says so.

        Safe to call as often as you like: it only buys when flat and only sells
        when holding, so repeated calls on the same candle never double-trade.
        """
        with self._lock:
            self.store.set("last_check", int(time.time() * 1000))
            try:
                return self._run()
            except Exception as exc:  # keep the loop alive; surface the error
                self.store.log(f"Run failed: {exc}", "error")
                return {"ok": False, "error": str(exc)}

    def _run(self) -> dict:
        candles = self.market.closed()
        if len(candles) < self.s.params.sma_length:
            raise RuntimeError(f"only {len(candles)} closed candles, need {self.s.params.sma_length}")
        last = candles[-1]
        if not self.s.data_file and time.time() * 1000 - last.ts > 3 * DAY_MS:
            raise RuntimeError("latest closed candle is more than 2 days old; refusing to trade on stale data")

        price = self.market.price()
        held = self.position_qty()
        in_pos = held * price >= MIN_POSITION_VALUE
        sig = evaluate([c.close for c in candles], in_pos, self.s.params)
        result = {"ok": True, "candle_ts": last.ts, "action": sig.action, "reason": sig.reason}

        new_candle = self.store.get("last_candle_ts") != last.ts
        if self.paused:
            if new_candle and sig.action != "HOLD":
                self.store.log(f"{sig.action} signal ignored because trading is paused ({sig.reason})", "warn")
            result["action"] = "PAUSED"
        elif sig.action == "BUY":
            self._buy(price, last, sig.reason)
        elif sig.action == "SELL":
            self._sell(price, last, sig.reason)
        elif new_candle:
            self.store.log(f"Daily close {last.close:,.2f}: {sig.reason}")

        self.store.set("last_candle_ts", last.ts)
        self.store.set("last_signal", {**sig.__dict__, "candle_ts": last.ts})
        return result

    def _buy(self, price: float, candle: Candle, reason: str) -> None:
        _, quote = self.broker.balances()
        spend = quote * self.s.allocation
        try:
            fill = self.broker.market_buy(spend, price)
        except OrderError as exc:
            self.store.log(f"BUY signal but order not placed: {exc}", "error")
            raise
        self.store.set("position_qty", self.position_qty() + fill["qty"])
        self._record("BUY", fill, candle, reason)

    def _sell(self, price: float, candle: Candle, reason: str) -> None:
        base, _ = self.broker.balances()
        qty = min(self.position_qty(), base)
        try:
            fill = self.broker.market_sell(qty, price)
        except OrderError as exc:
            self.store.log(f"SELL signal but order not placed: {exc}", "error")
            raise
        self.store.set("position_qty", max(0.0, self.position_qty() - fill["qty"]))
        self._record("SELL", fill, candle, reason)

    def _record(self, side: str, fill: dict, candle: Candle, reason: str) -> None:
        self.store.add_order(ts=int(time.time() * 1000), mode=self.broker.mode, side=side, qty=fill["qty"],
                             price=fill["price"], cost=fill["cost"], fee=fill["fee"], candle_ts=candle.ts,
                             exchange_id=fill["id"], reason=reason)
        self.store.log(f"{side} {fill['qty']:.6f} {self.s.base} at {fill['price']:,.2f} "
                       f"({self.broker.mode}) - {reason}", "trade")

    # ----- reporting ---------------------------------------------------------
    def status(self) -> dict:
        try:
            price = self.market.price()
        except Exception as exc:
            price = None
            self.store.log(f"Price fetch failed: {exc}", "error")
        try:
            base, quote = self.broker.balances()
        except Exception as exc:
            base = quote = None
            self.store.log(f"Balance fetch failed: {exc}", "error")
        qty = self.position_qty()
        last_check = self.store.get("last_check")
        return {
            "mode": self.broker.mode,
            "exchange": self.s.exchange if not self.s.data_file else f"file: {self.s.data_file}",
            "symbol": self.s.symbol, "base": self.s.base, "quote": self.s.quote,
            "paused": self.paused,
            "price": price,
            "position_qty": qty,
            "in_position": price is not None and qty * price >= MIN_POSITION_VALUE,
            "balance_base": base, "balance_quote": quote,
            "equity": (quote + base * price) if None not in (base, quote, price) else None,
            "paper_start": self.s.paper_start_quote if self.broker.mode == "paper" else None,
            "allocation": self.s.allocation,
            "params": self.s.params.to_dict(),
            "last_signal": self.store.get("last_signal"),
            "last_check": last_check,
            "next_check": last_check + int(self.s.check_interval_min * 60_000) if last_check else None,
        }
