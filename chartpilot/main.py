"""ChartPilot entry point."""

from __future__ import annotations

import sys
import traceback

from PyQt6.QtWidgets import QApplication

from chartpilot.settings.config_store import default_config_dir
from chartpilot.ui.main_window import MainWindow


def _install_crash_logger() -> None:
    """A --windowed build has no console to see tracebacks in, so log
    uncaught exceptions to a file instead of losing them silently."""

    def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
        try:
            log_path = default_config_dir() / "crash.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as f:
                f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
                f.write("\n---\n")
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught


def main() -> int:
    _install_crash_logger()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
