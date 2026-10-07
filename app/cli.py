"""Command-line helpers.

    python -m app.cli set-password     create DASHBOARD_PASSWORD_HASH
    python -m app.cli setup-2fa        create TOTP_SECRET for an authenticator app
    python -m app.cli check-binance    test the API key in .env (balances and permissions)
"""
from __future__ import annotations

import getpass
import sys
import time

from .security import hash_password, new_totp_secret, totp_now, totp_uri


def set_password() -> None:
    while True:
        pw = getpass.getpass("New dashboard password (min 12 characters): ")
        if len(pw) < 12:
            print("Too short, use at least 12 characters (a passphrase of 3-4 words works well).")
            continue
        if getpass.getpass("Repeat it: ") != pw:
            print("The passwords did not match, try again.")
            continue
        break
    print("\nPut this line in your .env file (it is a hash, not the password):\n")
    print(f"DASHBOARD_PASSWORD_HASH={hash_password(pw)}\n")


def setup_2fa() -> None:
    from .config import load_settings
    user = load_settings(validate=False).dashboard_user
    secret = new_totp_secret()
    print("\n1. In Google Authenticator / Authy / 1Password, add an account and choose")
    print("   'enter a setup key', then type this key (time-based):\n")
    print(f"      {' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))}\n")
    print("   or open this link on the phone that has the app:\n")
    print(f"      {totp_uri(secret, user)}\n")
    code = input("2. Type the 6-digit code the app now shows: ").strip()
    if code not in (totp_now(secret), totp_now(secret, at=time.time() - 30)):
        sys.exit("That code does not match. Check the phone's clock is automatic and run this again.")
    print("\n3. Code verified. Put this line in your .env file and keep the key secret:\n")
    print(f"TOTP_SECRET={secret}\n")


def check_binance() -> None:
    from .broker import ExchangeBroker, make_exchange
    from .config import load_settings
    s = load_settings(validate=False)
    if not (s.api_key and s.api_secret):
        sys.exit("API_KEY and API_SECRET are not set in .env")
    print(f"Connecting to {s.exchange}{' TESTNET' if s.testnet else ''} for {s.symbol} ...")
    broker = ExchangeBroker(make_exchange(s.exchange, s.api_key, s.api_secret, s.testnet), s.symbol, s.testnet)
    info = broker.verify()
    if info["ok"]:
        base, quote = broker.balances()
        print(f"  OK. Free balance: {base:.8f} {s.base}, {quote:,.2f} {s.quote}")
    labels = {"can_trade": "Spot trading enabled", "can_withdraw": "Withdrawals enabled (must be NO)",
              "ip_restricted": "Restricted to trusted IPs"}
    for key, label in labels.items():
        if info.get(key) is not None:
            print(f"  {label}: {'YES' if info[key] else 'NO'}")
    if info.get("ip_restricted") is False:
        print("  WARNING: restrict this key to your droplet's IP address in Binance API Management.")
    if not info["ok"]:
        sys.exit(f"  FAILED: {info['error']}")


COMMANDS = {"set-password": set_password, "setup-2fa": setup_2fa, "check-binance": check_binance}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
