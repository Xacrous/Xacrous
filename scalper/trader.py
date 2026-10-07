"""The scalping state machine for one symbol.

IDLE --signal--> BUYING --filled--> HOLDING --take-profit filled / stop / time / flip--> IDLE

* Entry: post-only limit buy at the best bid (maker fee), or a market buy if maker
  entry is turned off. Cancelled if not filled within the timeout or the signal fades.
* Profit, two modes:
  - trail (default): once the price reaches the level that nets ``min_profit_pct`` after
    both fees, that level becomes a floor and the exit follows the price up, selling when
    it falls ``trail_pct`` below its peak. No time limit while trailing, so a big move can run.
  - fixed: a post-only limit sell sits at the price that nets ``min_profit_pct``.
* Before the profit level is reached, exits at market (taker) on: stop loss, max hold
  time, order book flipping bearish, or a manual "close now".
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .book import Book
from .brokers import OrderRejected
from .flow import Candles, TradeFlow
from .market import MarketInfo
from .settings import TradeParams
from .signals import EntrySignal, Snapshot
from .store import Store

IDLE, BUYING, HOLDING = "IDLE", "BUYING", "HOLDING"


class Trader:
    def __init__(self, params: TradeParams, market: MarketInfo, broker, store: Store,
                 flow: TradeFlow, candles: Candles, poll_s: float = 0.0, clock=time.time):
        self.p, self.m, self.broker, self.store = params, market, broker, store
        self.flow, self.candles = flow, candles
        self.book = Book()
        self.poll_s, self.clock = poll_s, clock
        self.signal = EntrySignal(params.signal)
        self.state = IDLE
        self.enabled = False
        self.halt_reason: str | None = None
        self.buy: dict | None = None
        self.pos: dict | None = None
        self.snap = Snapshot(blockers=["starting"])
        self.blocker: str | None = None
        self.cooldown_until = 0.0
        self.consecutive_losses = int(store.get(self._key("consecutive_losses"), 0))
        self.maker = self.taker = 0.001
        self._last_poll = 0.0
        self._close_requested = False
        self._bear_since: float | None = None
        self._last_log: dict[str, float] = {}

    # ----- setup ---------------------------------------------------------------------
    def _key(self, name: str) -> str:
        return f"{self.broker.mode}:{self.m.symbol_id}:{name}"

    async def start(self) -> None:
        self.maker, self.taker = await self.broker.fees()
        saved = self.store.get(self._key("trader")) if self.broker.mode != "paper" else None
        if saved and (saved.get("pos") or saved.get("buy")):
            self.state, self.pos, self.buy = saved["state"], saved.get("pos"), saved.get("buy")
            self.log(f"Resumed {self.state} from the last run", "warn")
        if hasattr(self.broker, "cancel_stray_orders"):
            keep = {o["id"] for o in (self.buy, (self.pos or {}).get("tp")) if o}
            n = await self.broker.cancel_stray_orders(keep)
            if n:
                self.log(f"Cancelled {n} leftover bot order(s) from a previous run", "warn")

    def _persist(self) -> None:
        if self.broker.mode != "paper":
            self.store.set(self._key("trader"), {"state": self.state, "pos": self.pos, "buy": self.buy})

    def log(self, msg: str, level: str = "info", every_s: float = 0.0) -> None:
        """Log, optionally at most once per ``every_s`` for repeating messages."""
        now = self.clock()
        if every_s and now - self._last_log.get(msg, 0) < every_s:
            return
        self._last_log[msg] = now
        self.store.log(f"[{self.m.symbol_id}] {msg}", level)

    # ----- controls (applied by the next step) ----------------------------------------------
    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if on:
            self.halt_reason = None
            self.consecutive_losses = 0
            self.store.set(self._key("consecutive_losses"), 0)
        self.log("Trading started" if on else "Trading paused: no new entries", "info" if on else "warn")

    def request_close(self) -> None:
        self._close_requested = True

    @property
    def flat(self) -> bool:
        return self.state == IDLE and not self.pos and not self.buy

    # ----- fee maths ---------------------------------------------------------------------
    @property
    def entry_fee(self) -> float:
        return self.maker if self.p.use_maker_entry else self.taker

    @property
    def exit_fee(self) -> float:
        return self.taker if self.p.exit_mode == "trail" else self.maker  # trailing exits sell at market

    def target_gross_pct(self) -> float:
        """Price rise needed to net ``min_profit_pct`` after both fees."""
        return ((1 + self.p.min_profit_pct / 100) / ((1 - self.entry_fee) * (1 - self.exit_fee)) - 1) * 100

    # ----- main step -------------------------------------------------------------------------
    async def step(self, book: Book) -> None:
        self.book = book
        now = self.clock()
        self.snap = self.signal.evaluate(book, self.flow, self.candles, self.target_gross_pct(), now)
        try:
            if self.state == IDLE:
                await self._maybe_enter(now)
            elif self.state == BUYING:
                await self._manage_entry(now)
            elif self.state == HOLDING:
                await self._manage_position(now)
        except OrderRejected as exc:
            self.log(f"Order rejected: {exc}", "warn")
            self.cooldown_until = now + max(self.p.cooldown_s, 5)
        except Exception as exc:  # noqa: BLE001 - keep trading loop alive, surface the error
            self.log(f"Error in {self.state}: {type(exc).__name__}: {exc}", "error", every_s=30)

    def _poll_due(self, now: float) -> bool:
        if now - self._last_poll >= self.poll_s:
            self._last_poll = now
            return True
        return False

    # ----- IDLE -> BUYING ---------------------------------------------------------------------
    def _risk_block(self, now: float) -> str | None:
        if self.halt_reason:
            return self.halt_reason
        if not self.enabled:
            return "paused"
        if now < self.cooldown_until:
            return f"cooldown {self.cooldown_until - now:.0f}s"
        if self.daily_pnl() <= -self.p.max_daily_loss:
            return f"daily loss limit of {self.p.max_daily_loss:g} {self.m.quote} reached"
        if len(self.store.trades_since(now - 3600, self.broker.mode)) >= self.p.max_trades_per_hour:
            return f"{self.p.max_trades_per_hour} trades in the last hour"
        return None

    def daily_pnl(self) -> float:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return sum(t["pnl"] for t in self.store.trades_since(midnight, self.broker.mode)
                   if t["symbol"] == self.m.symbol_id)

    async def _maybe_enter(self, now: float) -> None:
        self.blocker = self._risk_block(now)
        if self.blocker or not self.snap.buy:
            return
        price = self.book.best_bid
        if self.book.spread > self.m.tick * 1.5:  # step one tick inside the spread: first in queue, still maker
            price = self.m.price_down(price + self.m.tick)
        qty = self.m.qty_down(self.p.order_quote / price)
        if not self.m.sellable(qty * (1 - self.entry_fee), price):
            self.blocker = (f"order size {self.p.order_quote:g} {self.m.quote} is below Binance's minimum "
                            f"({self.m.min_notional:g} {self.m.quote}); raise it")
            return
        _, quote_free = await self.broker.balances()
        if quote_free < qty * price:
            self.blocker = f"not enough {self.m.quote}: {quote_free:.8g} free"
            self.log(self.blocker, "warn", every_s=300)
            return
        if self.p.use_maker_entry:
            self.buy = {**await self.broker.limit_buy_maker(qty, price), "placed": now}
            self.state = BUYING
            self.log(f"BUY limit {qty:g} @ {price:g} (score {self.snap.score:+.2f})")
            self._persist()
        else:
            order = await self.broker.market_buy(qty * price)
            self.log(f"BUY market {order['filled']:g} @ {order['cost'] / order['filled']:g} "
                     f"(score {self.snap.score:+.2f})")
            await self._on_entry_filled(order, now)

    # ----- BUYING ------------------------------------------------------------------------------
    async def _manage_entry(self, now: float) -> None:
        o = self.buy
        if self._poll_due(now):
            o = self.buy = {**await self.broker.fetch(o["id"]), "placed": o["placed"]}
        if o["status"] == "closed":
            await self._on_entry_filled(o, now)
            return
        if o["status"] in ("canceled", "rejected"):
            self.log(f"Buy order {o['status']} by the exchange", "warn")
            self._reset_idle(now)
            return
        faded = self.snap.score < self.p.signal.entry_score / 2
        timed_out = now - o["placed"] > self.p.entry_timeout_s
        if not (faded or timed_out or self._close_requested or not self.enabled):
            return
        o = await self.broker.cancel(o["id"])
        why = "timed out" if timed_out else "signal faded" if faded else "paused"
        if o["filled"] > 0 and self.m.sellable(o["filled"] - o["fee_base"], self.book.best_bid):
            self.log(f"Buy {why}; partially filled {o['filled']:g}, managing that")
            await self._on_entry_filled(o, now)
        else:
            if o["filled"] > 0:
                self.log(f"Buy {why}; partial fill {o['filled']:g} is below the minimum order and stays "
                         f"in your wallet as dust", "warn")
            else:
                self.log(f"Buy cancelled: {why}")
            self._close_requested = False
            self._reset_idle(now, cooldown=False)

    async def _on_entry_filled(self, o: dict, now: float) -> None:
        held = o["filled"] - o["fee_base"]
        avg = o["cost"] / o["filled"]
        # Quote-valued cost of what was bought: price paid + any fee not taken from the coin (e.g. BNB).
        cost = o["cost"] + max(0.0, o["fee_quote"] - o["fee_base"] * avg)
        # Lot-size rounding leaves "dust" that cannot be sold on its own. The bot remembers it (with its
        # cost) and adds it to the next sell, so the take-profit is priced on the true cost per coin.
        base_free, _ = await self.broker.balances()
        carry = self.store.get(self._key("carry"), {"qty": 0.0, "cost": 0.0})
        carry_qty = max(0.0, min(carry["qty"], base_free - held))
        carry_cost = carry["cost"] * (carry_qty / carry["qty"]) if carry["qty"] else 0.0
        pool_qty, pool_cost = held + carry_qty, cost + carry_cost
        unit = pool_cost / pool_qty
        sell_qty = self.m.qty_down(min(pool_qty, base_free))
        mode = self.p.exit_mode
        # The price that nets min_profit_pct: sold by a resting maker order (fixed) or at market (trail).
        tp = self.m.price_up(unit * (1 + self.p.min_profit_pct / 100) / (1 - self.maker))
        floor = self.m.price_up(unit * (1 + self.p.min_profit_pct / 100) / (1 - self.taker))
        self.pos = {"entry_ts": now, "avg": avg, "unit_cost": unit, "qty": pool_qty, "sell_qty": sell_qty,
                    "cost": pool_cost, "entry_fee": o["fee_quote"], "mode": mode,
                    "tp_price": tp if mode == "fixed" else floor, "floor_price": floor,
                    "trailing": False, "peak": None, "trail_stop": None,
                    "stop_price": avg * (1 - self.p.stop_loss_pct / 100), "tp": None, "sells": []}
        self.buy = None
        self.state = HOLDING
        target = self.pos["tp_price"]
        what = "take-profit" if mode == "fixed" else "trailing starts at"
        self.log(f"Bought {held:g} @ {avg:g}; {what} {target:g} (+{(target / avg - 1) * 100:.3f}%), "
                 f"stop {self.pos['stop_price']:g}")
        if mode == "fixed":
            await self._place_tp()
        self._persist()

    async def _place_tp(self) -> None:
        pos = self.pos
        price = pos["tp_price"]
        if self.book.ready and price <= self.book.best_bid:
            price = self.m.price_up(self.book.best_bid + self.m.tick)  # stay a maker; still >= the minimum
        pos["tp"] = await self.broker.limit_sell_maker(pos["sell_qty"], price)

    async def _sell_at_least(self, now: float, reason: str) -> None:
        """Profitable exit that can never sell below the minimum-profit price.

        An immediate-or-cancel limit sell at the floor fills only at the floor or better. If the price
        has already gapped below it, the unsold part waits as a resting sell at the minimum-profit price
        (the stop loss still protects it) instead of being dumped for less.
        """
        pos = self.pos
        base_free, _ = await self.broker.balances()
        qty = self.m.qty_down(min(pos["sell_qty"], base_free))
        o = await self.broker.limit_sell_ioc(qty, pos["floor_price"])
        if o["filled"]:
            pos["sells"].append(o)
        left = self.m.qty_down(qty - o["filled"])
        if not left or not self.m.sellable(left, pos["floor_price"]):
            await self._close_trade(now, reason)
            return
        self.log(f"Price fell below the minimum-profit level before the sell; {left:g} {self.m.base} now "
                 f"waits for {pos['tp_price']:g} with the stop loss still active", "warn")
        pos.update(mode="fixed", trailing=False, sell_qty=left, timer_start=now)
        await self._place_tp()
        self._persist()

    # ----- HOLDING -----------------------------------------------------------------------------
    async def _manage_position(self, now: float) -> None:
        pos = self.pos
        if pos["tp"] and self._poll_due(now):
            pos["tp"] = await self.broker.fetch(pos["tp"]["id"])
            if pos["tp"]["status"] == "closed":
                pos["sells"].append(pos["tp"])
                await self._close_trade(now, "take profit")
                return
            if pos["tp"]["status"] in ("canceled", "rejected"):
                self.log("Take-profit order was cancelled outside the bot; replacing it", "warn")
                if pos["tp"]["filled"]:
                    pos["sells"].append(pos["tp"])
                pos["sell_qty"] = self.m.qty_down(pos["sell_qty"] - pos["tp"]["filled"])
                pos["tp"] = None
        bid = self.book.best_bid if self.book.ready else None
        trail = pos.get("mode") == "trail"
        # Start trailing once the bid is above the floor, so the floor sits below the price (no instant sell).
        if trail and bid is not None and not pos["trailing"] and bid > pos["floor_price"]:
            pos.update(trailing=True, peak=bid)
            self.log(f"Minimum profit reached at {bid:g}: now trailing {self.p.trail_pct:g}% below the peak, "
                     f"never selling below {pos['floor_price']:g}")
        reason = None
        if self._close_requested:
            reason = "closed manually"
        elif pos.get("trailing"):
            if bid is not None:
                pos["peak"] = max(pos["peak"], bid)
                stop = max(pos["floor_price"], self.m.price_down(pos["peak"] * (1 - self.p.trail_pct / 100)))
                if stop != pos["trail_stop"]:
                    pos["trail_stop"] = stop
                    self._persist()
                if bid <= stop:
                    await self._sell_at_least(now, f"trailing stop (peak {pos['peak']:g})")
                    return
        elif bid is not None and bid <= pos["stop_price"]:
            reason = "stop loss"
        elif now - pos.get("timer_start", pos["entry_ts"]) >= self.p.max_hold_s:
            reason = "time limit"
        elif self._bearish_for(now) >= self.p.signal.exit_confirm_s:
            reason = "order book turned bearish"
        if reason:
            await self._exit_market(now, reason)
        elif not trail and pos["tp"] is None:
            await self._place_tp()
            self._persist()

    def _bearish_for(self, now: float) -> float:
        if self.snap.score > self.p.signal.exit_score:
            self._bear_since = None
            return 0.0
        if self._bear_since is None:
            self._bear_since = now
        return now - self._bear_since

    async def _exit_market(self, now: float, reason: str) -> None:
        pos = self.pos
        remaining = pos["sell_qty"]
        if pos["tp"]:
            final = await self.broker.cancel(pos["tp"]["id"])
            pos["tp"] = None
            if final["filled"]:
                pos["sells"].append(final)
                remaining -= final["filled"]
            if final["status"] == "closed":
                await self._close_trade(now, "take profit")
                return
        base_free, _ = await self.broker.balances()
        qty = self.m.qty_down(min(remaining, base_free))
        if qty > 0 and self.m.sellable(qty, self.book.best_bid):
            pos["sells"].append(await self.broker.market_sell(qty))
        elif qty > 0:
            self.log(f"{qty:g} {self.m.base} left is below the minimum order and stays as dust", "warn")
        await self._close_trade(now, reason)

    async def _close_trade(self, now: float, reason: str) -> None:
        pos = self.pos
        sold = sum(s["filled"] for s in pos["sells"])
        gross = sum(s["cost"] for s in pos["sells"])
        fees = pos["entry_fee"] + sum(s["fee_quote"] for s in pos["sells"])
        proceeds = gross - sum(s["fee_quote"] for s in pos["sells"])
        unsold = max(0.0, pos["qty"] - sold)
        cost = sold * pos["unit_cost"]            # leftover dust is carried to the next trade at cost
        self.store.set(self._key("carry"), {"qty": unsold, "cost": unsold * pos["unit_cost"]})
        pnl = proceeds - cost
        exit_price = gross / sold if sold else 0.0
        self.store.add_trade(mode=self.broker.mode, symbol=self.m.symbol_id, entry_ts=pos["entry_ts"],
                             exit_ts=now, qty=sold, entry_price=pos["avg"], exit_price=exit_price, fees=fees,
                             pnl=pnl, pnl_pct=pnl / cost * 100 if cost else 0.0, reason=reason)
        level = "trade" if pnl >= 0 else "loss"
        self.log(f"SOLD {sold:g} @ {exit_price:g} ({reason}): {pnl:+.8g} {self.m.quote} "
                 f"({pnl / cost * 100 if cost else 0:+.3f}%)", level)
        self.consecutive_losses = 0 if pnl >= 0 else self.consecutive_losses + 1
        self.store.set(self._key("consecutive_losses"), self.consecutive_losses)
        self._close_requested = False
        self._reset_idle(now)
        if self.consecutive_losses >= self.p.max_consecutive_losses:
            self._halt(f"{self.consecutive_losses} losing trades in a row")
        elif self.daily_pnl() <= -self.p.max_daily_loss:
            self._halt(f"daily loss limit of {self.p.max_daily_loss:g} {self.m.quote} reached")

    def _halt(self, reason: str) -> None:
        self.halt_reason = f"halted: {reason}"
        self.enabled = False
        self.log(f"Trading stopped, {reason}. Review, then press Start to continue.", "error")

    def _reset_idle(self, now: float, cooldown: bool = True) -> None:
        self.state, self.pos, self.buy = IDLE, None, None
        self._bear_since = None
        self.signal.reset()
        if cooldown:
            self.cooldown_until = now + self.p.cooldown_s
        self._persist()

    # ----- reporting ---------------------------------------------------------------------------
    def status(self) -> dict:
        pos = None
        if self.pos:
            bid = self.book.best_bid if self.book.ready else self.pos["avg"]
            value = self.pos["qty"] * bid * (1 - self.taker)  # includes carried dust, as does cost
            stop = self.pos.get("trail_stop")
            pos = {**{k: v for k, v in self.pos.items() if k not in ("sells",)},
                   "locked_pct": (stop * (1 - self.taker) / self.pos["unit_cost"] - 1) * 100 if stop else None,
                   "age_s": self.clock() - self.pos["entry_ts"],
                   "unrealized": value - self.pos["cost"],
                   "unrealized_pct": (value / self.pos["cost"] - 1) * 100}
        return {"state": self.state, "enabled": self.enabled, "halt_reason": self.halt_reason,
                "blocker": self.blocker, "buy_order": self.buy, "position": pos,
                "fees": {"maker_pct": self.maker * 100, "taker_pct": self.taker * 100},
                "target_gross_pct": self.target_gross_pct(), "consecutive_losses": self.consecutive_losses,
                "daily_pnl": self.daily_pnl(), "signal": self.snap.to_dict()}
