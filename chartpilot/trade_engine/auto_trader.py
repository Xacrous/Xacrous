"""Auto-trade execution engine for the Trade tab.

Places REAL spot orders (or Binance Testnet orders, depending on how the
`ExchangeClient` it's given was configured) the instant a qualifying signal
fires, sized from the user's configured dollar amount, and watches the live
ticker to close the position the instant price crosses either exit level.

This is a *software-managed* exit rather than an exchange-side OCO order:
from the moment a position opens until it closes, ChartPilot itself is what
detects the take-profit/stop-loss cross and sends the closing market sell.
If the app loses its connection (closed, network drop, machine sleeps)
while a position is open, that position has NO server-side protection
until ChartPilot reconnects. This is a deliberate v1 tradeoff — the
sandbox this was built in cannot reach Binance at all, so a native OCO
order's exact parameter behavior (stopLimitPrice, listClientOrderId, etc.)
could not be verified against a real order book. Verify this thoroughly on
Binance Spot Testnet, and keep the app running for as long as a position
is open, before ever pointing it at a live account.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

from chartpilot.data_fetcher.exchange_client import ExchangeClient
from chartpilot.signal_engine.registry import get_strategy
from chartpilot.ta_engine.indicators import CANDLE_LIMIT, compute

logger = logging.getLogger(__name__)

# Rolling in-memory candle buffer cap — generous relative to CANDLE_LIMIT so
# the buffer never needs to grow unbounded across a long-running session.
_MAX_CANDLES_BUFFER = 2000


@dataclass
class OpenPosition:
    symbol: str
    entry_price: float
    quantity: float
    take_profit: float
    stop_loss: float
    opened_at: str


@dataclass
class ClosedTrade:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_quote: float
    pnl_pct: float
    reason: str  # "take_profit" | "stop_loss"
    opened_at: str
    closed_at: str


def compute_exit_levels(entry_price: float, profit_pct: float, loss_pct: float) -> tuple[float, float]:
    """Take-profit / stop-loss prices from the user-configured percentages."""
    take_profit = entry_price * (1 + profit_pct / 100.0)
    stop_loss = entry_price * (1 - loss_pct / 100.0)
    return take_profit, stop_loss


def compute_pnl(entry_price: float, exit_price: float, quantity: float) -> tuple[float, float]:
    """Realized P&L in quote currency and as a percent of the position's
    cost basis (entry_price * quantity)."""
    pnl_quote = (exit_price - entry_price) * quantity
    pnl_pct = (exit_price / entry_price - 1.0) * 100.0
    return pnl_quote, pnl_pct


class AutoTraderWorker(QThread):
    trade_opened = pyqtSignal(object)  # OpenPosition
    trade_closed = pyqtSignal(object)  # ClosedTrade
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        exchange_client: ExchangeClient,
        symbol: str,
        timeframe: str,
        strategy_id: str,
        quote_amount: float,
        profit_pct: float,
        loss_pct: float,
    ) -> None:
        super().__init__()
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy_id = strategy_id
        self.quote_amount = quote_amount
        self.profit_pct = profit_pct
        self.loss_pct = loss_pct
        self._stop_event = threading.Event()
        self._df: pd.DataFrame | None = None
        self.position: OpenPosition | None = None

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(5000)

    def run(self) -> None:
        try:
            self.status_changed.emit("Loading history…")
            limit = CANDLE_LIMIT["trade"]
            df = self.exchange_client.get_candles(self.symbol, self.timeframe, limit=limit)
            df.attrs["symbol"] = self.symbol
            df.attrs["timeframe"] = self.timeframe
            self._df = df
        except Exception as exc:  # noqa: BLE001 — surface any seed-fetch failure to the UI
            logger.exception("Auto-trader failed to seed history for %s %s", self.symbol, self.timeframe)
            self.error.emit(f"Couldn't load history: {exc}")
            return

        self.status_changed.emit("● Watching for signals…")
        try:
            self.exchange_client.subscribe_live(self.symbol, self.timeframe, self._on_update, self._stop_event)
        except Exception as exc:  # noqa: BLE001 — surface any feed-startup failure to the UI
            logger.exception("Auto-trader live feed failed for %s %s", self.symbol, self.timeframe)
            self.error.emit(str(exc))

    def _on_update(self, kind: str, payload: dict) -> None:
        if kind == "ticker":
            self._on_ticker(payload)
        elif kind == "kline":
            self._on_kline(payload)
        elif kind == "error":
            logger.warning("Auto-trader feed error: %s", payload.get("message"))

    def _on_kline(self, payload: dict) -> None:
        if self._df is None:
            return
        new_row = {
            "open_time": payload["open_time"], "open": payload["open"], "high": payload["high"],
            "low": payload["low"], "close": payload["close"], "volume": payload["volume"],
        }
        df = self._df
        if len(df) and int(df["open_time"].iloc[-1]) == int(payload["open_time"]):
            df.iloc[-1, df.columns.get_indexer(list(new_row.keys()))] = list(new_row.values())
        else:
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            if len(df) > _MAX_CANDLES_BUFFER:
                df = df.iloc[-_MAX_CANDLES_BUFFER:].reset_index(drop=True)
        df.attrs["symbol"] = self.symbol
        df.attrs["timeframe"] = self.timeframe
        self._df = df

        if self.position is not None:
            return  # one position open at a time — don't stack entries on repeat signals

        try:
            indicators = compute(df, mode="trade")
        except ValueError:
            return  # not enough candles in the trailing window yet
        strategy = get_strategy(self.strategy_id)
        signal = strategy.evaluate(df, indicators)
        if signal is not None and signal.direction == "long":
            self._open_position()

    def _open_position(self) -> None:
        try:
            order = self.exchange_client.place_market_buy_quote(self.symbol, self.quote_amount)
        except Exception as exc:  # noqa: BLE001 — surface any order failure to the UI, don't crash the feed
            logger.exception("Auto-trader buy failed for %s", self.symbol)
            self.error.emit(f"Buy order failed: {exc}")
            return

        entry_price = float(order.get("average") or order.get("price") or 0.0)
        quantity = float(order.get("filled") or order.get("amount") or 0.0)
        if entry_price <= 0 or quantity <= 0:
            self.error.emit("Buy order returned no fill price/quantity — position not opened.")
            return

        take_profit, stop_loss = compute_exit_levels(entry_price, self.profit_pct, self.loss_pct)
        self.position = OpenPosition(
            symbol=self.symbol, entry_price=entry_price, quantity=quantity,
            take_profit=take_profit, stop_loss=stop_loss,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        self.status_changed.emit(f"● Position open @ {entry_price:g} — watching TP {take_profit:g} / SL {stop_loss:g}")
        self.trade_opened.emit(self.position)

    def _on_ticker(self, payload: dict) -> None:
        if self.position is None:
            return
        last = payload.get("last")
        if last is None:
            return
        last = float(last)
        if last >= self.position.take_profit:
            self._close_position(last, "take_profit")
        elif last <= self.position.stop_loss:
            self._close_position(last, "stop_loss")

    def _close_position(self, trigger_price: float, reason: str) -> None:
        position = self.position
        if position is None:
            return
        try:
            order = self.exchange_client.place_market_sell(self.symbol, position.quantity)
        except Exception as exc:  # noqa: BLE001 — surface any order failure, keep the position tracked open
            logger.exception("Auto-trader sell failed for %s", self.symbol)
            self.error.emit(f"Sell order failed, position still open: {exc}")
            return  # keep self.position set so the next tick retries the close

        exit_price = float(order.get("average") or order.get("price") or trigger_price)
        pnl_quote, pnl_pct = compute_pnl(position.entry_price, exit_price, position.quantity)
        trade = ClosedTrade(
            symbol=position.symbol, entry_price=position.entry_price, exit_price=exit_price,
            quantity=position.quantity, pnl_quote=pnl_quote, pnl_pct=pnl_pct, reason=reason,
            opened_at=position.opened_at, closed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.position = None
        self.status_changed.emit(f"● Watching for signals… (last trade {pnl_pct:+.2f}%)")
        self.trade_closed.emit(trade)
