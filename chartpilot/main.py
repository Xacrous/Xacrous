"""ChartPilot entry point."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from chartpilot.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
