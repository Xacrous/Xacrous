"""Login security: password hashing, TOTP two-factor codes, sessions and lockout.

Only the Python standard library is used so there is nothing extra to audit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from urllib.parse import quote

# ----- passwords (scrypt) ------------------------------------------------------
_N, _R, _P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt:{_N}:{_R}:{_P}:{salt.hex()}:{key.hex()}"  # no "$": .env files expand it


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, key = stored.split(":")
        if algo != "scrypt":
            return False
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=64 * 1024 * 1024, dklen=len(key) // 2)
        return hmac.compare_digest(test.hex(), key)
    except (ValueError, TypeError):
        return False


# ----- TOTP (RFC 6238, works with Google Authenticator, Authy, 1Password...) ---
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(secret: str, account: str, issuer: str = "BTC Trend Bot") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


def _totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret: str, at: float | None = None) -> str:
    return _totp_at(secret, int((at or time.time()) // 30))


class TotpVerifier:
    """Accepts the current code or one 30 s step either side, and never the same code twice."""

    def __init__(self, secret: str):
        self.secret = secret
        self._last_counter = -1
        self._lock = threading.Lock()

    def verify(self, code: str, at: float | None = None) -> bool:
        code = (code or "").strip().replace(" ", "")
        if not (len(code) == 6 and code.isdigit()):
            return False
        now = int((at or time.time()) // 30)
        with self._lock:
            for counter in (now - 1, now, now + 1):
                if counter > self._last_counter and hmac.compare_digest(_totp_at(self.secret, counter), code):
                    self._last_counter = counter  # block replay of this and older codes
                    return True
        return False


# ----- sessions ------------------------------------------------------------------
class Sessions:
    """Server-side sessions kept in memory: a restart logs everyone out."""

    def __init__(self, idle_minutes: float = 60, max_hours: float = 12):
        self.idle = idle_minutes * 60
        self.max_age = max_hours * 3600
        self._s: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._s[_digest(token)] = {"user": user, "created": now, "seen": now}
        return token

    def get(self, token: str | None) -> dict | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            s = self._s.get(_digest(token))
            if not s:
                return None
            if now - s["seen"] > self.idle or now - s["created"] > self.max_age:
                self._s.pop(_digest(token), None)
                return None
            s["seen"] = now
            return s

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._s.pop(_digest(token), None)

    def revoke_all(self) -> None:
        with self._lock:
            self._s.clear()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ----- brute-force protection ----------------------------------------------------
class LoginLimiter:
    """Locks an IP after ``per_ip`` failures, and all logins after ``total`` failures, within ``window``."""

    def __init__(self, per_ip: int = 5, total: int = 20, window_s: float = 900):
        self.per_ip, self.total, self.window = per_ip, total, window_s
        self._fails: list[tuple[float, str]] = []
        self._lock = threading.Lock()

    def _recent(self, now: float) -> list[tuple[float, str]]:
        self._fails = [f for f in self._fails if now - f[0] < self.window]
        return self._fails

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            recent = self._recent(now)
            return len(recent) >= self.total or sum(1 for _, i in recent if i == ip) >= self.per_ip

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails.append((time.time(), ip))

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails = [f for f in self._fails if f[1] != ip]
