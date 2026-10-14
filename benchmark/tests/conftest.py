"""Shared test setup. Tests must never reach the network: this guard enforces it.

Only loopback connections are allowed (none of the tests need even those today;
the fake worlds and the proxy talk over stdio pipes). Model clients are always
fakes or ``httpx.MockTransport``.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

_real_connect = socket.socket.connect
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _guarded_connect(self: socket.socket, address: object) -> object:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and str(host) not in _LOOPBACK:
        raise RuntimeError(f"tests must not use the network (tried to reach {host})")
    return _real_connect(self, address)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "POLYGRAPH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
