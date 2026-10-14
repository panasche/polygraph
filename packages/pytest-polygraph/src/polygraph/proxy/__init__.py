"""The message-level MCP proxy ("the troublemaker in the middle").

- :mod:`polygraph.proxy.core` — transport-independent engine (faults + ledger)
- :mod:`polygraph.proxy.stdio` — wrapper command around a server subprocess
- :mod:`polygraph.proxy.http` — Streamable HTTP server in front of an upstream URL
"""

from __future__ import annotations

from typing import Any

__all__ = ["HttpProxy", "ProxyEngine", "Reply", "ServerThread", "run_stdio_proxy"]


def __getattr__(name: str) -> Any:
    if name in {"ProxyEngine", "Reply"}:
        from . import core

        return getattr(core, name)
    if name == "run_stdio_proxy":
        from .stdio import run_stdio_proxy

        return run_stdio_proxy
    if name in {"HttpProxy", "ServerThread"}:
        from . import http

        return getattr(http, name)
    raise AttributeError(name)
