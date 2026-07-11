"""First-launch compliance disclaimer (Section 1's Compliance Notice)."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QVBoxLayout
from qfluentwidgets import BodyLabel, PrimaryPushButton, SubtitleLabel

from chartpilot import DISCLAIMER


class DisclaimerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Before you start")
        self.setMinimumWidth(480)
        self.setModal(True)

        title = SubtitleLabel("Educational tool — not financial advice", self)
        body = BodyLabel(DISCLAIMER, self)
        body.setWordWrap(True)

        acknowledge_button = PrimaryPushButton("I Understand", self)
        acknowledge_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addStretch(1)
        layout.addWidget(acknowledge_button)
