"""Backtest/History screen (Phase 3): run a historical replay for the
current symbol/timeframe/strategy and browse the forward signal log —
every real signal ChartPilot has generated, auto-resolved as it plays out.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
)

from chartpilot.data_fetcher.exchange_client import ExchangeClient, SymbolNotFoundError
from chartpilot.signal_engine.backtester import BacktestResult, run_backtest
from chartpilot.signal_engine.base_strategy import Mode
from chartpilot.signal_engine.registry import get_strategy, list_strategies
from chartpilot.signal_engine.signal_log import SignalLog
from chartpilot.ui.analysis_view import TIMEFRAMES

logger = logging.getLogger(__name__)

_HISTORY_COLUMNS = ["Generated", "Symbol", "Timeframe", "Strategy", "Direction", "Confidence", "R:R", "Status"]
_TRADE_COLUMNS = ["Entry candle", "Direction", "Confidence", "R:R", "Outcome"]


class _BacktestWorker(QThread):
    succeeded = pyqtSignal(object)  # BacktestResult
    failed = pyqtSignal(str)

    def __init__(self, exchange_client: ExchangeClient, symbol: str, timeframe: str, mode: Mode, strategy_id: str, candle_count: int) -> None:
        super().__init__()
        self.exchange_client = exchange_client
        self.symbol = symbol
        self.timeframe = timeframe
        self.mode = mode
        self.strategy_id = strategy_id
        self.candle_count = candle_count

    def run(self) -> None:
        try:
            df = self.exchange_client.get_candles(self.symbol, self.timeframe, limit=self.candle_count)
            df.attrs["symbol"] = self.symbol
            df.attrs["timeframe"] = self.timeframe
            strategy = get_strategy(self.strategy_id)
            result = run_backtest(df, self.mode, strategy)
            self.succeeded.emit(result)
        except SymbolNotFoundError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — surface any backtest failure to the UI
            logger.exception("Backtest failed for %s %s", self.symbol, self.timeframe)
            self.failed.emit(f"Backtest failed: {exc}")


class BacktestInterface(QWidget):
    def __init__(self, exchange_client: ExchangeClient, signal_log: SignalLog, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("backtestInterface")
        self.exchange_client = exchange_client
        self.signal_log = signal_log
        self._worker: _BacktestWorker | None = None
        self._mode: Mode = "swing"

        self.symbol_edit = LineEdit(self)
        self.symbol_edit.setText("BTCUSDT")
        self.symbol_edit.setFixedWidth(120)

        self.mode_selector = SegmentedWidget(self)
        self.mode_selector.addItem(routeKey="swing", text="Swing", onClick=lambda: self._on_mode_changed("swing"))
        self.mode_selector.addItem(routeKey="scalp", text="Scalp", onClick=lambda: self._on_mode_changed("scalp"))
        self.mode_selector.addItem(routeKey="trade", text="Trade", onClick=lambda: self._on_mode_changed("trade"))
        self.mode_selector.setCurrentItem("swing")
        self.mode_selector.setFixedHeight(33)
        self.mode_selector.setMaximumWidth(210)

        self.timeframe_combo = ComboBox(self)
        self.strategy_combo = ComboBox(self)
        self._strategy_ids: list[str] = []
        self._populate_mode_controls("swing")

        self.candle_count_spin = SpinBox(self)
        self.candle_count_spin.setRange(300, 1000)
        self.candle_count_spin.setSingleStep(100)
        self.candle_count_spin.setValue(500)

        self.run_button = PrimaryPushButton("Run Backtest", self)
        self.run_button.clicked.connect(self.run_backtest)

        controls_row = QHBoxLayout()
        controls_row.addWidget(self.symbol_edit)
        controls_row.addWidget(self.mode_selector)
        controls_row.addWidget(self.timeframe_combo)
        controls_row.addWidget(self.strategy_combo)
        controls_row.addWidget(self.candle_count_spin)
        controls_row.addWidget(self.run_button)
        controls_row.addStretch(1)

        self.summary_label = StrongBodyLabel("Run a backtest to see results.", self)
        self.trades_table = TableWidget(self)
        self.trades_table.setColumnCount(len(_TRADE_COLUMNS))
        self.trades_table.setHorizontalHeaderLabels(_TRADE_COLUMNS)
        self.trades_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)

        history_header = QHBoxLayout()
        history_header.addWidget(SubtitleLabel("Signal History", self))
        history_header.addStretch(1)
        refresh_history_button = PushButton("Refresh", self)
        refresh_history_button.clicked.connect(self.refresh_history)
        export_button = PushButton("Export CSV", self)
        export_button.clicked.connect(self._export_csv)
        history_header.addWidget(refresh_history_button)
        history_header.addWidget(export_button)

        self.history_table = TableWidget(self)
        self.history_table.setColumnCount(len(_HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(_HISTORY_COLUMNS)
        self.history_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout(self)
        layout.addLayout(controls_row)
        layout.addWidget(self.summary_label)
        layout.addWidget(BodyLabel("Trades", self))
        layout.addWidget(self.trades_table, stretch=1)
        layout.addLayout(history_header)
        layout.addWidget(self.history_table, stretch=1)

        self.refresh_history()

    def _populate_mode_controls(self, mode: Mode) -> None:
        timeframes = TIMEFRAMES[mode]
        self.timeframe_combo.blockSignals(True)
        self.timeframe_combo.clear()
        self.timeframe_combo.addItems(timeframes)
        self.timeframe_combo.blockSignals(False)

        self.strategy_combo.blockSignals(True)
        self.strategy_combo.clear()
        self._strategy_ids = []
        for strategy in list_strategies(mode=mode):
            self.strategy_combo.addItem(strategy.display_name)
            self._strategy_ids.append(strategy.id)
        self.strategy_combo.blockSignals(False)

    def _on_mode_changed(self, mode: Mode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._populate_mode_controls(mode)

    def run_backtest(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        symbol = self.symbol_edit.text().strip().upper()
        timeframe = self.timeframe_combo.currentText()
        strategy_id = self._strategy_ids[self.strategy_combo.currentIndex()]
        candle_count = self.candle_count_spin.value()

        self.run_button.setEnabled(False)
        self.summary_label.setText("Running backtest…")

        self._worker = _BacktestWorker(self.exchange_client, symbol, timeframe, self._mode, strategy_id, candle_count)
        self._worker.succeeded.connect(self._on_backtest_done)
        self._worker.failed.connect(self._on_backtest_error)
        self._worker.finished.connect(lambda: self.run_button.setEnabled(True))
        self._worker.start()

    def _on_backtest_done(self, result: BacktestResult) -> None:
        win_rate = f"{result.win_rate * 100:.1f}%" if result.win_rate is not None else "n/a"
        avg_rr = f"{result.avg_reward_risk:.2f}" if result.avg_reward_risk is not None else "n/a"
        expectancy = f"{result.expectancy:+.2f}R" if result.expectancy is not None else "n/a"
        self.summary_label.setText(
            f"{result.total_trades} trades · win rate {win_rate} · avg R:R {avg_rr} · expectancy {expectancy}"
        )
        self.trades_table.setRowCount(len(result.trades))
        for row, trade in enumerate(result.trades):
            values = [
                str(trade.entry_time_ms),
                trade.direction,
                str(trade.confidence),
                f"{trade.reward_risk_ratio:.2f}",
                trade.outcome,
            ]
            for col, value in enumerate(values):
                self.trades_table.setItem(row, col, QTableWidgetItem(value))

    def _on_backtest_error(self, message: str) -> None:
        self.summary_label.setText(f"⚠ {message}")

    def refresh_history(self) -> None:
        rows = self.signal_log.list_recent(200)
        self.history_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            values = [
                entry["generated_at"], entry["symbol"], entry["timeframe"], entry["strategy"],
                entry["direction"], str(entry["confidence"]), f"{entry['reward_risk_ratio']:.2f}", entry["status"],
            ]
            for col, value in enumerate(values):
                self.history_table.setItem(row, col, QTableWidgetItem(value))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export signal history", "chartpilot_signal_history.csv", "CSV files (*.csv)")
        if not path:
            return
        self.signal_log.export_csv(path)
