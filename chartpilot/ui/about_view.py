"""About screen: version info, compliance disclaimer, and third-party attribution.

Ships the Lightweight Charts attribution notice + tradingview.com link from
day one (Apache-2.0 license condition, Section 8's licensing mitigation).
"""

from __future__ import annotations

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, HyperlinkLabel, SubtitleLabel, TitleLabel

from chartpilot import DISCLAIMER, __version__


class AboutInterface(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutInterface")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        layout.addWidget(TitleLabel(f"ChartPilot v{__version__}", self))
        layout.addWidget(BodyLabel(
            "Educational technical-analysis tool for Binance spot markets. "
            "Decision support only — no order execution.", self,
        ))

        layout.addWidget(SubtitleLabel("Compliance notice", self))
        disclaimer_label = BodyLabel(DISCLAIMER, self)
        disclaimer_label.setWordWrap(True)
        layout.addWidget(disclaimer_label)

        layout.addWidget(SubtitleLabel("Third-party attribution", self))
        chart_attribution = BodyLabel(
            "Charting powered by TradingView Lightweight Charts™, "
            "licensed under the Apache License 2.0.", self,
        )
        chart_attribution.setWordWrap(True)
        layout.addWidget(chart_attribution)

        link = HyperlinkLabel(QUrl("https://www.tradingview.com/lightweight-charts/"), "tradingview.com/lightweight-charts", self)
        layout.addWidget(link)

        layout.addStretch(1)
