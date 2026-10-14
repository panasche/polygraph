"""Identifiers and secrets.

Resource ids look like ``rep_01j9x...``: a type prefix plus a lower-case
Crockford base32 ULID (48-bit millisecond time + 80 random bits). They sort by
creation time, which keeps keyset pagination simple and lets ``max(id)`` mean
"latest".
"""

from __future__ import annotations

import secrets
import string
import time

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"
_BASE62 = string.digits + string.ascii_letters

UPLOAD_TOKEN_PREFIX = "pgp_"  # noqa: S105 (public prefix)


def ulid(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"


def base62(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 62)
        out.append(_BASE62[r])
    return "".join(reversed(out))


def new_upload_token() -> str:
    """``pgp_`` + 32 random bytes in base62 (left-padded to a fixed 43 chars)."""
    return UPLOAD_TOKEN_PREFIX + base62(secrets.token_bytes(32)).rjust(43, "0")


def token_display_prefix(token: str) -> str:
    return token[: len(UPLOAD_TOKEN_PREFIX) + 4]
