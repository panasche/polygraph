"""Apply a :class:`RecipeStack` to a synchronous MCP client.

Used by the scripted dry-run adapter (``polygraph.testing.McpClient``) and by the
recipe unit tests (a fake client). Anything with ``call_tool(name, args,
timeout=...)`` and ``list_tools(refresh=...)`` returning ``ToolResult``-like objects
works.
"""

from __future__ import annotations

from typing import Any

from .core import RecipeStack, ToolCallResult, run_sync


class RecipeClient:
    """A drop-in wrapper: same methods as the wrapped client, recipes in between."""

    def __init__(self, client: Any, stack: RecipeStack, *, server: str = "*") -> None:
        self._client = client
        self._stack = stack
        self._server = server

    def __getattr__(self, name: str) -> Any:  # tools_stale, close, protocol_version, ...
        return getattr(self._client, name)

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        tools = self._client.list_tools(refresh=refresh)
        return self._stack.on_tools(self._server, [dict(t) for t in tools])

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        if self._stack.definition(name) is None:
            self.list_tools()

        async def nxt(args: dict[str, Any]) -> ToolCallResult:
            res = self._client.call_tool(name, args, timeout=timeout)
            return ToolCallResult(
                text=str(getattr(res, "text", "")),
                structured=getattr(res, "structured", None),
                is_error=bool(getattr(res, "is_error", False)),
                raw=res,
            )

        out = run_sync(self._stack.call(self._server, name, dict(arguments or {}), nxt))
        if not out.changed and out.raw is not None:
            return out.raw
        from polygraph.testing.client import ToolResult

        return ToolResult(text=out.text, structured=out.structured, is_error=out.is_error)
