"""Small crypto helpers: hashing, signed values, CSRF tokens, encryption at rest."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken


def sha256_hex(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Signer:
    """HMAC-SHA256 signed, time-limited values (for the OAuth state cookie)."""

    def __init__(self, secret: str, purpose: str) -> None:
        self._key = hashlib.sha256(f"{purpose}:{secret}".encode()).digest()

    def _mac(self, payload: bytes) -> str:
        return _b64(hmac.new(self._key, payload, hashlib.sha256).digest())

    def sign(self, value: str) -> str:
        payload = f"{int(time.time())}.{value}".encode()
        return f"{_b64(payload)}.{self._mac(payload)}"

    def unsign(self, signed: str, max_age_s: int) -> str | None:
        try:
            body, mac = signed.rsplit(".", 1)
            payload = _unb64(body)
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(mac.encode(), self._mac(payload).encode()):
            return None
        try:
            ts_text, value = payload.decode().split(".", 1)
            ts = int(ts_text)
        except (ValueError, UnicodeDecodeError):
            return None
        if ts > time.time() + 60 or time.time() - ts > max_age_s:
            return None
        return value


def csrf_for_session(secret: str, session_token: str) -> str:
    """CSRF token bound to the session (the double-submit cookie value)."""
    mac = hmac.new(secret.encode(), b"csrf:" + session_token.encode(), hashlib.sha256)
    return _b64(mac.digest())


def hash_ip(secret: str, ip: str) -> str:
    """Keyed hash of an IP address. Raw IPs are never stored or logged."""
    return hmac.new(secret.encode(), b"ip:" + ip.encode(), hashlib.sha256).hexdigest()[:32]


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


class Encryptor:
    """Fernet encryption for GitHub user tokens at rest."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str | None:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            return None
