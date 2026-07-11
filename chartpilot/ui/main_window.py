"""MSFluentWindow shell: navigation rail + Analysis/Settings/About (Section 6.1).

Backtest/History is Phase 3 scope (it depends on the backtester) and is not
wired into the nav rail yet.
"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import MSFluentWindow, NavigationItemPosition, setTheme, Theme

from chartpilot.data_fetcher.cache import CandleCache
from chartpilot.data_fetcher.exchange_client import ExchangeClient
from chartpilot.settings.config_store import ConfigStore
from chartpilot.settings.settings_dialog import SettingsDialog
from chartpilot.ui.about_view import AboutInterface
from chartpilot.ui.analysis_view import AnalysisInterface
from chartpilot.ui.disclaimer import DisclaimerDialog


class MainWindow(MSFluentWindow):
    def __init__(self, config_dir: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("ChartPilot")
        self.resize(1280, 800)

        self.config_store = ConfigStore(config_dir)
        prefs = self.config_store.load()
        setTheme(Theme.DARK if prefs.theme == "dark" else Theme.LIGHT)

        cache_path = self.config_store.config_dir / "cache.sqlite3"
        self.candle_cache = CandleCache(cache_path)
        api_key = self.config_store.get_api_key()
        self.exchange_client = ExchangeClient(
            self.candle_cache,
            api_key=api_key[0] if api_key else None,
            api_secret=api_key[1] if api_key else None,
        )

        self.analysis_interface = AnalysisInterface(
            self.exchange_client, prefs.symbol, prefs.timeframe, prefs.mode, prefs.strategy, self
        )
        self.about_interface = AboutInterface(self)

        self.addSubInterface(self.analysis_interface, FIF.MARKET, "Analysis")
        self.addSubInterface(self.about_interface, FIF.INFO, "About", position=NavigationItemPosition.BOTTOM)

        self.navigationInterface.addItem(
            routeKey="settings",
            icon=FIF.SETTING,
            text="Settings",
            onClick=self._open_settings,
            position=NavigationItemPosition.BOTTOM,
        )

        if not prefs.disclaimer_acknowledged:
            self._show_disclaimer()

    def _show_disclaimer(self) -> None:
        dialog = DisclaimerDialog(self)
        dialog.exec()
        prefs = self.config_store.load()
        prefs.disclaimer_acknowledged = True
        self.config_store.save(prefs)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config_store, self)
        if dialog.exec():
            api_key = self.config_store.get_api_key()
            self.exchange_client = ExchangeClient(
                self.candle_cache,
                api_key=api_key[0] if api_key else None,
                api_secret=api_key[1] if api_key else None,
            )
            self.analysis_interface.exchange_client = self.exchange_client

    def closeEvent(self, event) -> None:
        self.analysis_interface.shutdown()
        prefs = self.config_store.load()
        prefs.symbol = self.analysis_interface.symbol_edit.text().strip().upper()
        prefs.timeframe = self.analysis_interface.timeframe_combo.currentText()
        prefs.mode = self.analysis_interface.mode
        prefs.strategy = self.analysis_interface.selected_strategy_id
        self.config_store.save(prefs)
        self.candle_cache.close()
        super().closeEvent(event)
