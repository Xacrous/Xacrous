"""Start the scalping bot and open the dashboard.   python run.py"""
from __future__ import annotations

import sys
import threading
import webbrowser


def keep_windows_awake() -> None:
    """Stop Windows from sleeping while the bot runs (the screen may still turn off)."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def main() -> None:
    import uvicorn

    from scalper.engine import Engine
    from scalper.settings import load_env
    from scalper.web import create_app

    try:
        env = load_env()
    except ValueError as exc:
        sys.exit(f"Configuration error in .env: {exc}")
    url = f"http://127.0.0.1:{env.port}"
    print(f"\n  Binance scalper - {env.mode.upper()}{' TESTNET' if env.testnet else ''} mode")
    print(f"  Dashboard: {url}   (press Ctrl+C here to stop the bot)\n")
    keep_windows_awake()
    if "--no-browser" not in sys.argv:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(Engine(env)), host=env.host, port=env.port, log_level="warning")


if __name__ == "__main__":
    main()
