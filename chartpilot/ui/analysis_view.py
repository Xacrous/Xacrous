"""Main analysis screen: symbol/timeframe/strategy controls, chart, and signal panel.

Phase 1 scope: swing mode only, manual refresh only (Section 7). The fetch
pipeline runs on a background thread so a slow network call never freezes
the UI (Section 8's target of <2s fetch / <500ms render).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from chartpilot.chart_view.chart_widget import ChartWidget
from chartpilot.data_fetcher.exchange_client import ExchangeClient, SymbolNotFoundError
from chartpilot.signal_engine.base_strategy import Signal
from chartpilot.signal_engine.registry import list_strategies
from chartpilot.ta_engine.indicators import IndicatorSet, compute

logger = logging.getLogger(__name__)

SWING_TIMEFRAMES = ["1h", "4h", "1d"]
CANDLE_LIMIT = 250


class _SignalWorker(QThread):
    succeeded = pyqtSignal(object, object, object)  # df, indicators, signal
    failed = pyqtSignal(str)

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str, strategy_id: str) -> None:
        super().__init__()
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy_id = strategy_id

    def run(self) -> None:
        from chartpilot.signal_engine.registry import get_strategy

        try:
            df = self.exchange_client.get_candles(self.symbol, self.timeframe, limit=CANDLE_LIMIT)
            df.attrs["symbol"] = self.symbol
            df.attrs["timeframe"] = self.timeframe
            indicators = compute(df, mode="swing")
            strategy = get_strategy(self.strategy_id)
            signal = strategy.evaluate(df, indicators)
            self.succeeded.emit(df, indicators, signal)
        except SymbolNotFoundError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — surface any fetch/compute failure to the UI
            logger.exception("Signal pipeline failed for %s %s", self.symbol, self.timeframe)
            self.failed.emit(f"Couldn't load {self.symbol} {self.timeframe}: {exc}")


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
        layout.addWidget(CaptionLabel("Rationale", self))
        layout.addLayout(self.rationale_container)
        layout.addStretch(1)

        self.show_empty_state()

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
        self._clear_rationale()

    def show_signal(self, signal: Signal) -> None:
        arrow = "▲ LONG" if signal.direction == "long" else "▼ SHORT"
        self.direction_label.setText(arrow)
        self.entry_label.setText(f"Entry: {signal.entry:g}")
        self.tp_label.setText(f"TP: {signal.take_profit:g}")
        self.sl_label.setText(f"SL: {signal.stop_loss:g}")
        self.confidence_label.setText(f"Confidence: {signal.confidence}%")
        self.rr_label.setText(f"R:R: {signal.reward_risk_ratio:.2f}")
        self._clear_rationale()
        for line in signal.rationale:
            label = CaptionLabel(line, self)
            label.setWordWrap(True)
            self.rationale_container.addWidget(label)
            self.rationale_labels.append(label)


class AnalysisInterface(QWidget):
    def __init__(self, exchange_client: ExchangeClient, default_symbol: str, default_timeframe: str, default_strategy_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("analysisInterface")
        self.exchange_client = exchange_client
        self._worker: _SignalWorker | None = None

        self.symbol_edit = LineEdit(self)
        self.symbol_edit.setText(default_symbol)
        self.symbol_edit.setFixedWidth(120)

        self.timeframe_combo = ComboBox(self)
        self.timeframe_combo.addItems(SWING_TIMEFRAMES)
        if default_timeframe in SWING_TIMEFRAMES:
            self.timeframe_combo.setCurrentText(default_timeframe)

        self.strategy_combo = ComboBox(self)
        self._strategy_ids: list[str] = []
        for strategy in list_strategies(mode="swing"):
            self.strategy_combo.addItem(strategy.display_name)
            self._strategy_ids.append(strategy.id)
        if default_strategy_id in self._strategy_ids:
            self.strategy_combo.setCurrentIndex(self._strategy_ids.index(default_strategy_id))

        self.refresh_button = PrimaryPushButton("⟳ Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)

        self.status_label = CaptionLabel("", self)

        top_bar = QHBoxLayout()
        top_bar.addWidget(self.symbol_edit)
        top_bar.addWidget(self.timeframe_combo)
        top_bar.addWidget(self.strategy_combo)
        top_bar.addWidget(self.refresh_button)
        top_bar.addStretch(1)
        top_bar.addWidget(self.status_label)

        self.chart_widget = ChartWidget(self)
        self.signal_panel = SignalPanel(self)

        splitter = QSplitter(self)
        splitter.addWidget(self.chart_widget)
        splitter.addWidget(self.signal_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        layout = QVBoxLayout(self)
        layout.addLayout(top_bar)
        layout.addWidget(splitter)

    @property
    def selected_strategy_id(self) -> str:
        return self._strategy_ids[self.strategy_combo.currentIndex()]

    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        symbol = self.symbol_edit.text().strip().upper()
        timeframe = self.timeframe_combo.currentText()
        strategy_id = self.selected_strategy_id

        self.refresh_button.setEnabled(False)
        self.status_label.setText("Loading…")

        self._worker = _SignalWorker(self.exchange_client, symbol, timeframe, strategy_id)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self.refresh_button.setEnabled(True))
        self._worker.start()

    def _on_success(self, df, indicators: IndicatorSet, signal: Signal | None) -> None:
        self.chart_widget.render(df, indicators, signal)
        if signal is not None:
            self.signal_panel.show_signal(signal)
        else:
            self.signal_panel.show_empty_state()
        last_close = float(df["close"].iloc[-1])
        self.status_label.setText(f"{last_close:g} · ● Live (last refresh)")

    def _on_error(self, message: str) -> None:
        self.signal_panel.show_empty_state()
        self.status_label.setText(f"⚠ {message}")
