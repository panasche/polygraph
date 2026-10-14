"""Helpers for testing with Polygraph: an MCP client and two scripted reference agents.

The scripted agents use no LLM. They are deterministic stand-ins for a sloppy
agent (:class:`NaiveAgent`) and a careful one (:class:`CarefulAgent`), so the
whole pipeline (proxy, faults, scorer, reports) can be shown and tested with
real, honest data.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CarefulAgent",
    "McpClient",
    "McpError",
    "NaiveAgent",
    "ScriptedAgent",
    "ToolResult",
]


def __getattr__(name: str) -> Any:
    if name in {"McpClient", "McpError", "ToolResult"}:
        from . import client

        return getattr(client, name)
    if name in {"CarefulAgent", "NaiveAgent", "ScriptedAgent"}:
        from . import agents

        return getattr(agents, name)
    raise AttributeError(name)
