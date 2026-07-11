"""Post-build smoke test for the packaged Windows executable. Launches it
with a pre-seeded config (skips the first-launch disclaimer modal, which
would otherwise block forever waiting for a click) and confirms it stays
running rather than crashing on startup. Surfaces crash.log on failure
since a --windowed build has no console to see tracebacks in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

EXE_PATH = Path("dist/ChartPilot/ChartPilot.exe")
RUN_SECONDS = 8


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "ChartPilot"
    return Path.home() / ".chartpilot"


def main() -> None:
    if not EXE_PATH.exists():
        print(f"ERROR: {EXE_PATH} not found", file=sys.stderr)
        sys.exit(1)

    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    prefs = {
        "symbol": "BTCUSDT", "timeframe": "4h", "mode": "swing",
        "strategy": "trend_following_ma_cross", "refresh_interval_seconds": 30,
        "theme": "dark", "disclaimer_acknowledged": True,
    }
    (config_dir / "preferences.json").write_text(json.dumps(prefs))
    crash_log = config_dir / "crash.log"
    if crash_log.exists():
        crash_log.unlink()

    print(f"Launching {EXE_PATH} ...")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    process = subprocess.Popen([str(EXE_PATH)], env=env)
    time.sleep(RUN_SECONDS)

    exit_code = process.poll()
    if exit_code is not None:
        print(f"ERROR: process exited early with code {exit_code}", file=sys.stderr)
        if crash_log.exists():
            print("--- crash.log ---", file=sys.stderr)
            print(crash_log.read_text(), file=sys.stderr)
        sys.exit(1)

    print(f"OK: still running after {RUN_SECONDS}s, terminating.")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()

    if crash_log.exists():
        print("WARNING: crash.log written despite the process staying alive:", file=sys.stderr)
        print(crash_log.read_text(), file=sys.stderr)
        sys.exit(1)

    print("EXE SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
