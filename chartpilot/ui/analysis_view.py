"""Main analysis screen: mode/symbol/timeframe/strategy controls, chart, and signal panel.

The fetch pipeline runs on a background thread so a slow network call never
freezes the UI (Section 8's target of <2s fetch / <500ms render). Manual
refresh (tier 3) does a full re-fetch + recompute; after each successful
refresh a WebSocket feed takes over for tier-1 sub-second ticker updates
and tier-2 candle-close recomputes (Section 6.4), without hitting the
network again. Switching Mode swaps the timeframe options and re-populates
the Strategy dropdown without touching any other code, per the registry's
whole design (Section 3).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

import pandas as pd
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFormLayout, QFrame, QHBoxLayout, QInputDialog, QScrollArea, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    DoubleSpinBox,
    InfoBar,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    SegmentedWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TransparentPushButton,
)

from chartpilot.chart_view.chart_widget import ChartWidget
from chartpilot.data_fetcher.exchange_client import ExchangeClient, SymbolNotFoundError
from chartpilot.settings.config_store import ConfigStore
from chartpilot.signal_engine.base_strategy import Mode, Signal
from chartpilot.signal_engine.pipeline import enrich_signal, resolve_pending_for_window
from chartpilot.signal_engine.registry import get_strategy, list_strategies
from chartpilot.signal_engine.signal_log import SignalLog
from chartpilot.ta_engine.indicators import CANDLE_LIMIT, IndicatorSet, compute
from chartpilot.trade_engine.auto_trader import AutoTraderWorker, ClosedTrade, OpenPosition

logger = logging.getLogger(__name__)

TIMEFRAMES: dict[Mode, list[str]] = {
    "swing": ["1h", "4h", "1d"],
    "scalp": ["1m", "5m", "15m"],
    "trade": ["1m", "5m", "15m"],
}
_LIVE_STOP_TIMEOUT_MS = 5000
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Safety cap on how far back infinite scroll-back can grow the in-memory
# chart frame — well beyond CANDLE_LIMIT (the single-fetch size), but bounded
# so an aggressive scroll-back session can't grow memory/indicator-compute
# cost unbounded.
MAX_CHART_CANDLES = 20000


class WatchlistPanel(QWidget):
    symbol_selected = pyqtSignal(str)

    def __init__(self, symbols: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(90)
        self._symbols: list[str] = []

        self._list_container = QWidget(self)
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setWidget(self._list_container)

        self._add_button = TransparentPushButton("+ Add", self)
        self._add_button.setToolTip("Add a symbol to the watchlist")
        self._add_button.clicked.connect(self._prompt_add_symbol)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(CaptionLabel("WATCHLIST", self))
        layout.addWidget(scroll_area, 1)
        layout.addWidget(self._add_button)

        for symbol in symbols:
            self.add_symbol(symbol)

    def add_symbol(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if not symbol or symbol in self._symbols:
            return
        self._symbols.append(symbol)
        button = TransparentPushButton(symbol.removesuffix("USDT"), self)
        button.setToolTip(symbol)
        button.clicked.connect(lambda checked=False, s=symbol: self.symbol_selected.emit(s))
        self._list_layout.addWidget(button)

    def _prompt_add_symbol(self) -> None:
        text, ok = QInputDialog.getText(self, "Add to watchlist", "Symbol (e.g. BTCUSDT):")
        if ok and text.strip():
            self.add_symbol(text)


class _SignalWorker(QThread):
    succeeded = pyqtSignal(object, object, object)  # df, indicators, signal
    failed = pyqtSignal(str)

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str, mode: Mode, strategy_id: str, signal_log: SignalLog | None) -> None:
        super().__init__()
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self.mode = mode
        self.strategy_id = strategy_id
        self.signal_log = signal_log

    def run(self) -> None:
        from chartpilot.signal_engine.registry import get_strategy

        try:
            limit = CANDLE_LIMIT[self.mode]
            df = self.exchange_client.get_candles(self.symbol, self.timeframe, limit=limit)
            df.attrs["symbol"] = self.symbol
            df.attrs["timeframe"] = self.timeframe
            indicators = compute(df, mode=self.mode)
            strategy = get_strategy(self.strategy_id)
            signal = strategy.evaluate(df, indicators)
            resolve_pending_for_window(df, self.signal_log)
            if signal is not None:
                enrich_signal(df, self.mode, strategy, signal, self.signal_log)
            self.succeeded.emit(df, indicators, signal)
        except SymbolNotFoundError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — surface any fetch/compute failure to the UI
            logger.exception("Signal pipeline failed for %s %s", self.symbol, self.timeframe)
            self.failed.emit(f"Couldn't load {self.symbol} {self.timeframe}: {exc}")


class _HistoryWorker(QThread):
    succeeded = pyqtSignal(object)  # older candles df (may be empty)
    failed = pyqtSignal(str)

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str, before_ms: int) -> None:
        super().__init__()
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self.before_ms = before_ms

    def run(self) -> None:
        try:
            df = self.exchange_client.get_candles_before(self.symbol, self.timeframe, self.before_ms)
            self.succeeded.emit(df)
        except Exception as exc:  # noqa: BLE001 — surface any pagination failure to the UI
            logger.exception("History pagination failed for %s %s", self.symbol, self.timeframe)
            self.failed.emit(str(exc))


class _LiveFeedThread(QThread):
    ticker_received = pyqtSignal(dict)
    kline_closed = pyqtSignal(dict)
    feed_error = pyqtSignal(str)

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str) -> None:
        super().__init__()
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            self.exchange_client.subscribe_live(self.symbol, self.timeframe, self._on_update, self._stop_event)
        except Exception as exc:  # noqa: BLE001 — surface any feed-startup failure to the UI
            logger.exception("Live feed failed for %s %s", self.symbol, self.timeframe)
            self.feed_error.emit(str(exc))

    def _on_update(self, kind: str, payload: dict) -> None:
        if kind == "ticker":
            self.ticker_received.emit(payload)
        elif kind == "kline":
            self.kline_closed.emit(payload)
        elif kind == "error":
            self.feed_error.emit(payload.get("message", "live feed error"))

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(_LIVE_STOP_TIMEOUT_MS)


class SignalPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(260)

        self.direction_label = SubtitleLabel("—", self)
        self.entry_label = BodyLabel("", self)
        self.tp_label = BodyLabel("", self)
        self.sl_label = BodyLabel("", self)
        self.confidence_label = StrongBodyLabel("", self)
        self.rr_label = BodyLabel("", self)
        self.win_rate_label = BodyLabel("", self)
        self.expiry_label = CaptionLabel("", self)
        self.rationale_labels: list[BodyLabel] = []
        self.rationale_container = QVBoxLayout()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(CaptionLabel("SIGNAL", self))
        layout.addWidget(self.direction_label)
        layout.addWidget(self.entry_label)
        layout.addWidget(self.tp_label)
        layout.addWidget(self.sl_label)
        layout.addWidget(self.confidence_label)
        layout.addWidget(self.rr_label)
        layout.addWidget(self.win_rate_label)
        layout.addWidget(self.expiry_label)
        layout.addWidget(CaptionLabel("Rationale", self))
        layout.addLayout(self.rationale_container)

        layout.addWidget(CaptionLabel("PREVIOUS SIGNALS", self))
        self._history_container = QWidget(self)
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(2)
        history_scroll = QScrollArea(self)
        history_scroll.setWidgetResizable(True)
        history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        history_scroll.setMaximumHeight(160)
        history_scroll.setWidget(self._history_container)
        layout.addWidget(history_scroll)
        self._history_labels: list[CaptionLabel] = []

        layout.addStretch(1)

        self.show_empty_state()
        self.show_history([])

    def _clear_rationale(self) -> None:
        while self.rationale_container.count():
            item = self.rationale_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rationale_labels = []

    def show_empty_state(self) -> None:
        self.direction_label.setText("No signal")
        self.entry_label.setText("No qualifying setup for this strategy right now.")
        self.tp_label.setText("")
        self.sl_label.setText("")
        self.confidence_label.setText("")
        self.rr_label.setText("")
        self.win_rate_label.setText("")
        self.expiry_label.setText("")
        self._clear_rationale()

    def show_signal(self, signal: Signal) -> None:
        arrow = "▲ LONG" if signal.direction == "long" else "▼ SHORT"
        self.direction_label.setText(arrow)
        self.entry_label.setText(f"Entry: {signal.entry:g}")
        self.tp_label.setText(f"TP: {signal.take_profit:g}")
        self.sl_label.setText(f"SL: {signal.stop_loss:g}")
        self.confidence_label.setText(f"Confidence: {signal.confidence}%")
        self.rr_label.setText(f"R:R: {signal.reward_risk_ratio:.2f}")
        if signal.historical_win_rate is not None:
            self.win_rate_label.setText(f"Historical WR: {signal.historical_win_rate * 100:.0f}%")
        else:
            self.win_rate_label.setText("Historical WR: not enough data yet")
        self.expiry_label.setText(f"Expires: {signal.expires_at}" if signal.expires_at else "")
        self._clear_rationale()
        for line in signal.rationale:
            label = CaptionLabel(line, self)
            label.setWordWrap(True)
            self.rationale_container.addWidget(label)
            self.rationale_labels.append(label)

    def show_history(self, rows: list[dict]) -> None:
        for label in self._history_labels:
            label.deleteLater()
        self._history_labels = []
        if not rows:
            empty = CaptionLabel("No previous signals yet.", self)
            self._history_layout.addWidget(empty)
            self._history_labels.append(empty)
            return
        status_text = {"pending": "… pending", "hit_tp": "✓ hit TP", "hit_sl": "✗ hit SL", "expired": "⏱ expired"}
        for row in rows:
            arrow = "▲" if row["direction"] == "long" else "▼"
            status = status_text.get(row["status"], row["status"])
            label = CaptionLabel(f"{arrow} {row['generated_at'][:16]} · entry {row['entry']:g} · {status}", self)
            label.setWordWrap(True)
            self._history_layout.addWidget(label)
            self._history_labels.append(label)


class AutoTradePanel(QWidget):
    """Trade tab-only control surface: dollar amount / TP% / SL% inputs,
    the Start/Stop toggle, and a running P&L scoreboard that resets every
    time Start is pressed. Purely a view — AnalysisInterface owns the
    actual AutoTraderWorker and pushes state into this panel."""

    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(220)
        self._running = False
        self._trades: list[ClosedTrade] = []

        self.mode_label = StrongBodyLabel("", self)

        self.amount_spin = DoubleSpinBox(self)
        self.amount_spin.setRange(1.0, 1_000_000.0)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setValue(50.0)
        self.amount_spin.setSuffix(" USDT")

        self.profit_spin = DoubleSpinBox(self)
        self.profit_spin.setRange(0.1, 100.0)
        self.profit_spin.setDecimals(2)
        self.profit_spin.setValue(2.0)
        self.profit_spin.setSuffix(" %")

        self.loss_spin = DoubleSpinBox(self)
        self.loss_spin.setRange(0.1, 100.0)
        self.loss_spin.setDecimals(2)
        self.loss_spin.setValue(1.0)
        self.loss_spin.setSuffix(" %")

        form = QFormLayout()
        form.addRow("Amount / trade", self.amount_spin)
        form.addRow("Take profit", self.profit_spin)
        form.addRow("Stop loss", self.loss_spin)

        self.start_stop_button = PrimaryPushButton("▶ Start Auto-Trade", self)
        self.start_stop_button.clicked.connect(self._on_button_clicked)

        self.status_label = CaptionLabel("Stopped", self)
        self.status_label.setWordWrap(True)

        self.pnl_label = StrongBodyLabel("$0.00 (0.00%)", self)
        self.trade_count_label = BodyLabel("0 trades · 0W / 0L", self)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(CaptionLabel("AUTO-TRADE", self))
        layout.addWidget(self.mode_label)
        layout.addLayout(form)
        layout.addWidget(self.start_stop_button)
        layout.addWidget(self.status_label)
        layout.addWidget(CaptionLabel("STATISTICS (since Start)", self))
        layout.addWidget(self.pnl_label)
        layout.addWidget(self.trade_count_label)
        layout.addStretch(1)

        self.set_mode_label(testnet=True)

    def _on_button_clicked(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()

    @property
    def amount(self) -> float:
        return self.amount_spin.value()

    @property
    def profit_pct(self) -> float:
        return self.profit_spin.value()

    @property
    def loss_pct(self) -> float:
        return self.loss_spin.value()

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_stop_button.setText("■ Stop Auto-Trade" if running else "▶ Start Auto-Trade")
        self.amount_spin.setEnabled(not running)
        self.profit_spin.setEnabled(not running)
        self.loss_spin.setEnabled(not running)

    def set_mode_label(self, testnet: bool) -> None:
        self.mode_label.setText("🧪 TESTNET (fake funds)" if testnet else "⚠ LIVE — REAL MONEY")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def reset_stats(self) -> None:
        self._trades = []
        self.pnl_label.setText("$0.00 (0.00%)")
        self.trade_count_label.setText("0 trades · 0W / 0L")

    def record_closed_trade(self, trade: ClosedTrade) -> None:
        self._trades.append(trade)
        total_quote = sum(t.pnl_quote for t in self._trades)
        total_cost_basis = sum(t.entry_price * t.quantity for t in self._trades)
        total_pct = (total_quote / total_cost_basis * 100.0) if total_cost_basis else 0.0
        wins = sum(1 for t in self._trades if t.pnl_quote > 0)
        losses = len(self._trades) - wins
        sign = "+" if total_quote >= 0 else ""
        self.pnl_label.setText(f"{sign}${total_quote:,.2f} ({sign}{total_pct:.2f}%)")
        self.trade_count_label.setText(f"{len(self._trades)} trades · {wins}W / {losses}L")


class AnalysisInterface(QWidget):
    def __init__(
        self,
        exchange_client: ExchangeClient,
        default_symbol: str,
        default_timeframe: str,
        default_mode: Mode,
        default_strategy_id: str,
        signal_log: SignalLog,
        refresh_interval_seconds: int = 30,
        fixed_mode: Mode | None = None,
        object_name: str = "analysisInterface",
        config_store: ConfigStore | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.exchange_client = exchange_client
        self.signal_log = signal_log
        self.config_store = config_store
        self._worker: _SignalWorker | None = None
        self._live_thread: _LiveFeedThread | None = None
        self._history_worker: _HistoryWorker | None = None
        self._auto_trader: AutoTraderWorker | None = None
        self._current_df: pd.DataFrame | None = None
        self._no_more_history = False
        self._fixed_mode = fixed_mode
        if fixed_mode is not None:
            self._mode: Mode = fixed_mode
        else:
            self._mode = default_mode if default_mode in TIMEFRAMES else "swing"

        self.symbol_edit = LineEdit(self)
        self.symbol_edit.setText(default_symbol)
        self.symbol_edit.setFixedWidth(120)

        # Compact two-item pill: pinned so it can never stretch to fill
        # whatever width the top bar's layout happens to give it. Omitted
        # entirely when the mode is fixed (e.g. the single-mode Trade tab)
        # since there's nothing to toggle between.
        if fixed_mode is None:
            self.mode_selector = SegmentedWidget(self)
            self.mode_selector.addItem(routeKey="swing", text="Swing", onClick=lambda: self._on_mode_changed("swing"))
            self.mode_selector.addItem(routeKey="scalp", text="Scalp", onClick=lambda: self._on_mode_changed("scalp"))
            self.mode_selector.setCurrentItem(self._mode)
            self.mode_selector.setFixedHeight(33)
            self.mode_selector.setMaximumWidth(140)
        else:
            self.mode_selector = None

        self.timeframe_combo = ComboBox(self)
        self.strategy_combo = ComboBox(self)
        self._strategy_ids: list[str] = []
        self._populate_mode_controls(self._mode, preferred_timeframe=default_timeframe, preferred_strategy_id=default_strategy_id)
        self.timeframe_combo.currentIndexChanged.connect(self._on_timeframe_changed)
        self.strategy_combo.currentIndexChanged.connect(self._on_strategy_changed)

        self.refresh_button = PrimaryPushButton("⟳ Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)

        # Fallback periodic refresh alongside the WS live feed — honors the
        # "Refresh interval" setting (previously stored but never acted on).
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self.set_refresh_interval_seconds(refresh_interval_seconds)

        self.status_label = CaptionLabel("", self)

        top_bar = QHBoxLayout()
        top_bar.addWidget(self.symbol_edit)
        if self.mode_selector is not None:
            top_bar.addWidget(self.mode_selector)
        top_bar.addWidget(self.timeframe_combo)
        top_bar.addWidget(self.strategy_combo)
        top_bar.addWidget(self.refresh_button)
        top_bar.addStretch(1)
        top_bar.addWidget(self.status_label)

        self.chart_widget = ChartWidget(self)
        self.chart_widget.bridge.more_history_requested.connect(self._on_more_history_requested)
        self.signal_panel = SignalPanel(self)

        splitter = QSplitter(self)
        splitter.addWidget(self.chart_widget)
        splitter.addWidget(self.signal_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        # Auto-trade control + stats panel: Trade tab only.
        self.auto_trade_panel: AutoTradePanel | None = None
        if fixed_mode == "trade" and config_store is not None:
            self.auto_trade_panel = AutoTradePanel(self)
            self.auto_trade_panel.start_requested.connect(self._on_auto_trade_start_requested)
            self.auto_trade_panel.stop_requested.connect(self._on_auto_trade_stop_requested)
            prefs = config_store.load()
            self.auto_trade_panel.amount_spin.setValue(prefs.auto_trade_amount_usdt)
            self.auto_trade_panel.profit_spin.setValue(prefs.auto_trade_profit_pct)
            self.auto_trade_panel.loss_spin.setValue(prefs.auto_trade_loss_pct)
            self.auto_trade_panel.set_mode_label(prefs.auto_trade_use_testnet)
            splitter.addWidget(self.auto_trade_panel)
            splitter.setStretchFactor(2, 0)

        self.watchlist_panel = WatchlistPanel(DEFAULT_WATCHLIST, self)
        self.watchlist_panel.symbol_selected.connect(self._on_watchlist_symbol_selected)

        content_layout = QVBoxLayout()
        content_layout.addLayout(top_bar)
        content_layout.addWidget(splitter)

        layout = QHBoxLayout(self)
        layout.addWidget(self.watchlist_panel)
        layout.addLayout(content_layout)

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def selected_strategy_id(self) -> str:
        return self._strategy_ids[self.strategy_combo.currentIndex()]

    def _populate_mode_controls(self, mode: Mode, preferred_timeframe: str | None = None, preferred_strategy_id: str | None = None) -> None:
        timeframes = TIMEFRAMES[mode]
        self.timeframe_combo.blockSignals(True)
        self.timeframe_combo.clear()
        self.timeframe_combo.addItems(timeframes)
        if preferred_timeframe in timeframes:
            self.timeframe_combo.setCurrentText(preferred_timeframe)
        self.timeframe_combo.blockSignals(False)

        self.strategy_combo.blockSignals(True)
        self.strategy_combo.clear()
        self._strategy_ids = []
        for strategy in list_strategies(mode=mode):
            self.strategy_combo.addItem(strategy.display_name)
            self._strategy_ids.append(strategy.id)
        if preferred_strategy_id in self._strategy_ids:
            self.strategy_combo.setCurrentIndex(self._strategy_ids.index(preferred_strategy_id))
        self.strategy_combo.blockSignals(False)

    def _on_watchlist_symbol_selected(self, symbol: str) -> None:
        self.symbol_edit.setText(symbol)
        self.refresh()

    def _on_mode_changed(self, mode: Mode) -> None:
        if mode == self._mode:
            return
        self._stop_live_feed()
        self._current_df = None
        self._no_more_history = False
        self._mode = mode
        self._populate_mode_controls(mode)
        self.refresh()

    def _on_timeframe_changed(self, _index: int) -> None:
        # Timeframe determines candle granularity, so this needs a fresh fetch.
        self.refresh()

    def _on_strategy_changed(self, _index: int) -> None:
        # No new candles needed — just re-evaluate the already-loaded window
        # against the newly selected strategy and update the chart/panel.
        if self._current_df is None:
            return
        self._recompute_and_render(self._current_df)

    def set_refresh_interval_seconds(self, seconds: int) -> None:
        """Periodic fallback refresh alongside the WS live feed, driven by
        the user's Settings > Refresh interval preference."""
        self._refresh_timer.start(max(1, int(seconds)) * 1000)

    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        symbol = self.symbol_edit.text().strip().upper()
        timeframe = self.timeframe_combo.currentText()
        mode = self._mode
        strategy_id = self.selected_strategy_id

        self.refresh_button.setEnabled(False)
        self.status_label.setText("Loading…")

        self._worker = _SignalWorker(self.exchange_client, symbol, timeframe, mode, strategy_id, self.signal_log)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self.refresh_button.setEnabled(True))
        self._worker.start()

    def _on_success(self, df, indicators: IndicatorSet, signal: Signal | None) -> None:
        self._current_df = df
        self._no_more_history = False
        strategy = get_strategy(self.selected_strategy_id)
        self._display_signal(df, indicators, signal, strategy)
        last_close = float(df["close"].iloc[-1])
        self.status_label.setText(f"{last_close:g} · ● Live (last refresh)")
        self._restart_live_feed(str(df.attrs.get("symbol", "")), str(df.attrs.get("timeframe", "")))

    def _restore_pending_signal(self, df: pd.DataFrame, strategy) -> Signal | None:
        """A strategy only returns non-None on the exact candle its setup
        completes — every render after that would otherwise clear the
        chart's TP/SL lines and the panel back to "No signal" even though
        the trade is still open. Fall back to the most recent still-pending
        row in the forward log for this exact context so it stays visible
        until it actually resolves."""
        if self.signal_log is None:
            return None
        symbol = str(df.attrs.get("symbol", ""))
        timeframe = str(df.attrs.get("timeframe", ""))
        row = self.signal_log.get_latest_pending(symbol, timeframe, strategy.id)
        if row is None:
            return None
        return Signal(
            symbol=row["symbol"], timeframe=row["timeframe"], mode=strategy.mode, strategy=row["strategy"],
            direction=row["direction"], entry=row["entry"], take_profit=row["take_profit"], stop_loss=row["stop_loss"],
            confidence=row["confidence"], reward_risk_ratio=row["reward_risk_ratio"],
            rationale=[f"Still-open signal from {row['generated_at']} — restored from the signal log."],
            generated_at=row["generated_at"], expires_at=row["expires_at"],
        )

    def _display_signal(self, df: pd.DataFrame, indicators: IndicatorSet, signal: Signal | None, strategy) -> None:
        if signal is None:
            signal = self._restore_pending_signal(df, strategy)
        self.chart_widget.render(df, indicators, signal)
        if signal is not None:
            self.signal_panel.show_signal(signal)
        else:
            self.signal_panel.show_empty_state()
        if self.signal_log is not None:
            symbol = str(df.attrs.get("symbol", ""))
            timeframe = str(df.attrs.get("timeframe", ""))
            rows = self.signal_log.list_for_symbol(symbol, timeframe=timeframe, strategy=strategy.id, limit=15)
            self.signal_panel.show_history(rows)

    def _on_error(self, message: str) -> None:
        self.signal_panel.show_empty_state()
        self.status_label.setText(f"⚠ {message}")

    def _restart_live_feed(self, symbol: str, timeframe: str) -> None:
        self._stop_live_feed()
        self._live_thread = _LiveFeedThread(self.exchange_client, symbol, timeframe)
        self._live_thread.ticker_received.connect(self._on_ticker_received)
        self._live_thread.kline_closed.connect(self._on_kline_closed)
        self._live_thread.feed_error.connect(self._on_live_feed_error)
        self._live_thread.start()

    def _stop_live_feed(self) -> None:
        if self._live_thread is not None:
            self._live_thread.stop()
            self._live_thread = None

    def _on_ticker_received(self, payload: dict) -> None:
        last = payload.get("last")
        if last is None:
            return
        pct = payload.get("percentage")
        pct_str = f" {pct:+.2f}% (24h)" if pct is not None else ""
        now = datetime.now().strftime("%H:%M:%S")
        self.status_label.setText(f"{last:g}{pct_str} · ● Live · updated {now}")
        self._update_live_candle_price(float(last))

    def _update_live_candle_price(self, last_price: float) -> None:
        """Tier-1 sub-second update: move the still-forming last candle's
        close/high/low with the live ticker price, without touching
        indicators or re-evaluating the strategy (that's tier-2, on candle
        close) — otherwise the chart's current candle sits frozen between
        candle closes even though the price is live underneath it."""
        if self._current_df is None or self._current_df.empty:
            return
        df = self._current_df
        idx = df.index[-1]
        df.at[idx, "close"] = last_price
        df.at[idx, "high"] = max(float(df.at[idx, "high"]), last_price)
        df.at[idx, "low"] = min(float(df.at[idx, "low"]), last_price)
        self.chart_widget.update_last_candle(df.loc[idx])

    def _recompute_and_render(self, df: pd.DataFrame) -> None:
        try:
            indicators = compute(df, mode=self._mode)
        except ValueError:
            return  # not enough candles in the trailing window yet
        strategy = get_strategy(self.selected_strategy_id)
        signal = strategy.evaluate(df, indicators)
        resolve_pending_for_window(df, self.signal_log)
        if signal is not None:
            enrich_signal(df, self._mode, strategy, signal, self.signal_log)
        self._display_signal(df, indicators, signal, strategy)

    def _on_kline_closed(self, payload: dict) -> None:
        if self._current_df is None:
            return
        df = self._current_df
        new_row = {
            "open_time": payload["open_time"], "open": payload["open"], "high": payload["high"],
            "low": payload["low"], "close": payload["close"], "volume": payload["volume"],
        }
        if len(df) and int(df["open_time"].iloc[-1]) == int(payload["open_time"]):
            df.iloc[-1, df.columns.get_indexer(list(new_row.keys()))] = list(new_row.values())
        else:
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            # Cap at MAX_CHART_CANDLES rather than CANDLE_LIMIT here: the
            # user may have paginated in far more history via scroll-back
            # than a single fetch carries, and a live candle close must not
            # silently discard it.
            if len(df) > MAX_CHART_CANDLES:
                df = df.iloc[-MAX_CHART_CANDLES:].reset_index(drop=True)
        symbol = self.symbol_edit.text().strip().upper()
        timeframe = self.timeframe_combo.currentText()
        df.attrs["symbol"] = symbol
        df.attrs["timeframe"] = timeframe
        self._current_df = df
        self._recompute_and_render(df)

    def _on_more_history_requested(self, oldest_time_sec: float) -> None:
        """The chart panned near its left (oldest-loaded) edge — fetch one
        more page of older candles in the background and prepend it."""
        if self._current_df is None or self._no_more_history:
            return
        if self._history_worker is not None and self._history_worker.isRunning():
            return
        symbol = str(self._current_df.attrs.get("symbol", ""))
        timeframe = str(self._current_df.attrs.get("timeframe", ""))
        before_ms = int(oldest_time_sec * 1000)

        self._history_worker = _HistoryWorker(self.exchange_client, symbol, timeframe, before_ms)
        self._history_worker.succeeded.connect(self._on_more_history_loaded)
        self._history_worker.failed.connect(self._on_more_history_failed)
        self._history_worker.start()

    def _on_more_history_loaded(self, older_df: pd.DataFrame) -> None:
        if self._current_df is None:
            return
        if older_df is None or older_df.empty:
            self._no_more_history = True
            self.chart_widget.mark_no_more_history()
            return

        symbol = str(self._current_df.attrs.get("symbol", ""))
        timeframe = str(self._current_df.attrs.get("timeframe", ""))
        merged = pd.concat([older_df, self._current_df], ignore_index=True)
        merged = merged.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)

        no_more = False
        if len(merged) > MAX_CHART_CANDLES:
            merged = merged.iloc[-MAX_CHART_CANDLES:].reset_index(drop=True)
            no_more = True  # hit the safety cap — stop paginating further back
        merged.attrs["symbol"] = symbol
        merged.attrs["timeframe"] = timeframe

        try:
            indicators = compute(merged, mode=self._mode)
        except ValueError:
            # Shouldn't happen since we only ever add candles, but stay
            # defensive rather than crash the pagination path.
            self.chart_widget.reset_loading_more()
            return

        self._current_df = merged
        self._no_more_history = no_more
        # Deliberately not re-running strategy.evaluate() here: the current
        # signal describes the latest candle and must not change just
        # because older history loaded further back on the chart.
        self.chart_widget.prepend_history(merged, indicators, no_more_history=no_more)

    def _on_more_history_failed(self, message: str) -> None:
        logger.warning("History pagination failed: %s", message)
        self.chart_widget.reset_loading_more()

    def _on_live_feed_error(self, message: str) -> None:
        logger.warning("Live feed error: %s", message)
        self.status_label.setText("⚠ Live feed reconnecting…")

    def _on_auto_trade_start_requested(self) -> None:
        panel = self.auto_trade_panel
        if panel is None or self.config_store is None or self._auto_trader is not None:
            return

        trading_key = self.config_store.get_trading_api_key()
        if trading_key is None:
            InfoBar.error(title="No trading API key", content="Add a trading-enabled Binance API key in Settings first.", parent=self)
            return

        prefs = self.config_store.load()
        if not prefs.auto_trade_use_testnet:
            box = MessageBox(
                "Start LIVE auto-trading?",
                "This places REAL orders on your Binance account with REAL money, fully "
                "autonomously — every qualifying VWAP Crossover signal will be traded "
                "immediately with no per-trade confirmation, until you click Stop. "
                "Continue?",
                self,
            )
            if not box.exec():
                return

        prefs.auto_trade_amount_usdt = panel.amount
        prefs.auto_trade_profit_pct = panel.profit_pct
        prefs.auto_trade_loss_pct = panel.loss_pct
        self.config_store.save(prefs)

        exchange_client = ExchangeClient(
            self.exchange_client.cache,
            api_key=trading_key[0], api_secret=trading_key[1],
            testnet=prefs.auto_trade_use_testnet,
        )
        symbol = self.symbol_edit.text().strip().upper()
        timeframe = self.timeframe_combo.currentText()
        strategy_id = self.selected_strategy_id

        self._auto_trader = AutoTraderWorker(
            exchange_client, symbol, timeframe, strategy_id,
            panel.amount, panel.profit_pct, panel.loss_pct,
        )
        self._auto_trader.status_changed.connect(panel.set_status)
        self._auto_trader.trade_opened.connect(self._on_auto_trade_opened)
        self._auto_trader.trade_closed.connect(self._on_auto_trade_closed)
        self._auto_trader.error.connect(self._on_auto_trade_error)

        panel.reset_stats()
        panel.set_mode_label(prefs.auto_trade_use_testnet)
        panel.set_running(True)
        self._auto_trader.start()

    def _on_auto_trade_stop_requested(self) -> None:
        panel = self.auto_trade_panel
        if self._auto_trader is not None:
            self._auto_trader.stop()
            self._auto_trader = None
        if panel is not None:
            panel.set_running(False)
            panel.set_status("Stopped")

    def _on_auto_trade_opened(self, position: OpenPosition) -> None:
        logger.info("Auto-trade opened: %s @ %s", position.symbol, position.entry_price)

    def _on_auto_trade_closed(self, trade: ClosedTrade) -> None:
        if self.auto_trade_panel is not None:
            self.auto_trade_panel.record_closed_trade(trade)

    def _on_auto_trade_error(self, message: str) -> None:
        logger.warning("Auto-trade error: %s", message)
        InfoBar.warning(title="Auto-trade", content=message, parent=self)

    def shutdown(self) -> None:
        self._refresh_timer.stop()
        self._stop_live_feed()
        if self._auto_trader is not None:
            self._auto_trader.stop()
            self._auto_trader = None
