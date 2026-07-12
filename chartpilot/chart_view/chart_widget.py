"""QWebEngineView host for the Lightweight Charts candlestick chart.

Data crosses into JS over a QWebChannel bridge: `render()` pushes a full
dataset (candles + overlays + volume + signal annotations) and
`update_last_candle()` handles the sub-second/candle-close updates without
a full re-render (Section 3's Chart Renderer contract).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView

from chartpilot.signal_engine.base_strategy import Signal
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.patterns import detect_fibonacci_retracement

_WEB_DIR = Path(__file__).parent / "web"


class ChartBridge(QObject):
    render_signal = pyqtSignal(str)
    update_last_candle_signal = pyqtSignal(str)
    prepend_history_signal = pyqtSignal(str)
    no_more_history_signal = pyqtSignal()
    history_request_failed_signal = pyqtSignal()

    # Emitted (oldest loaded candle's open_time, in seconds) when the chart
    # pans near the left edge of what's loaded and JS wants an older page —
    # the UI layer connects to this to kick off a background fetch.
    more_history_requested = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.is_ready = False

    @pyqtSlot()
    def ready(self) -> None:
        self.is_ready = True

    @pyqtSlot(float)
    def request_more_history(self, oldest_time_sec: float) -> None:
        self.more_history_requested.emit(oldest_time_sec)


def _series_to_points(times_ms: pd.Series, values: pd.Series) -> list[dict]:
    points = []
    for t, v in zip(times_ms, values):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        points.append({"time": int(t) // 1000, "value": round(float(v), 8)})
    return points


class ChartWidget(QWebEngineView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bridge = ChartBridge(self)
        self.channel = QWebChannel(self.page())
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self.load(QUrl.fromLocalFile(str(_WEB_DIR / "chart.html")))

    def _build_payload(self, df: pd.DataFrame, indicators: IndicatorSet) -> dict:
        times_ms = df["open_time"]
        candles = [
            {
                "time": int(t) // 1000,
                "open": round(float(o), 8),
                "high": round(float(h), 8),
                "low": round(float(low), 8),
                "close": round(float(c), 8),
            }
            for t, o, h, low, c in zip(times_ms, df["open"], df["high"], df["low"], df["close"])
        ]
        volume = [
            {
                "time": int(t) // 1000,
                "value": round(float(v), 8),
                "color": "#16C784" if c >= o else "#EA3943",
            }
            for t, v, o, c in zip(times_ms, df["volume"], df["open"], df["close"])
        ]
        if indicators.mode == "swing":
            overlays = {
                "sma20": _series_to_points(times_ms, indicators.sma20),
                "sma50": _series_to_points(times_ms, indicators.sma50),
                "sma200": _series_to_points(times_ms, indicators.sma200),
            }
            pivots = indicators.pivots.as_dict() if indicators.pivots is not None else None
            fib = detect_fibonacci_retracement(df, indicators.atr14)
            fibonacci = {"direction": fib.direction, "levels": {str(k): v for k, v in fib.levels.items()}} if fib is not None else None
            volume_profile = None
        else:
            overlays = {
                "ema9": _series_to_points(times_ms, indicators.ema9),
                "ema21": _series_to_points(times_ms, indicators.ema21),
                "bb_lower": _series_to_points(times_ms, indicators.bb_lower),
                "bb_mid": _series_to_points(times_ms, indicators.bb_mid),
                "bb_upper": _series_to_points(times_ms, indicators.bb_upper),
            }
            pivots = None
            fibonacci = None
            volume_profile = (
                [{"price_low": b.price_low, "price_high": b.price_high, "volume": b.volume} for b in indicators.volume_profile.bins]
                if indicators.volume_profile is not None
                else None
            )

        return {
            "candles": candles,
            "volume": volume,
            "overlays": overlays,
            "pivots": pivots,
            "fibonacci": fibonacci,
            "volume_profile": volume_profile,
        }

    def render(self, df: pd.DataFrame, indicators: IndicatorSet, signal: Signal | None) -> None:
        payload = self._build_payload(df, indicators)
        payload["signal"] = (
            {
                "direction": signal.direction,
                "entry": signal.entry,
                "take_profit": signal.take_profit,
                "stop_loss": signal.stop_loss,
            }
            if signal is not None
            else None
        )
        self.bridge.render_signal.emit(json.dumps(payload))

    def prepend_history(self, df: pd.DataFrame, indicators: IndicatorSet, no_more_history: bool = False) -> None:
        """Push a merged (older + already-loaded) candle set after a
        scroll-back pagination fetch. Deliberately doesn't touch the signal
        overlay — the current signal is about the latest candle and must
        not change just because older history loaded."""
        payload = self._build_payload(df, indicators)
        payload["no_more_history"] = no_more_history
        self.bridge.prepend_history_signal.emit(json.dumps(payload))

    def mark_no_more_history(self) -> None:
        self.bridge.no_more_history_signal.emit()

    def reset_loading_more(self) -> None:
        self.bridge.history_request_failed_signal.emit()

    def update_last_candle(self, candle_row: pd.Series) -> None:
        payload = {
            "candle": {
                "time": int(candle_row["open_time"]) // 1000,
                "open": round(float(candle_row["open"]), 8),
                "high": round(float(candle_row["high"]), 8),
                "low": round(float(candle_row["low"]), 8),
                "close": round(float(candle_row["close"]), 8),
            },
            "volume": {
                "time": int(candle_row["open_time"]) // 1000,
                "value": round(float(candle_row["volume"]), 8),
                "color": "#16C784" if candle_row["close"] >= candle_row["open"] else "#EA3943",
            },
        }
        self.bridge.update_last_candle_signal.emit(json.dumps(payload))
