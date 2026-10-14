"""Pydantic AI.

MCP client: ``pydantic_ai.mcp.MCPToolset`` (built on the FastMCP client) with a
FastMCP ``StdioTransport`` / ``StreamableHttpTransport`` and its defaults
(``cache_tools=True``, refreshed on ``tools/list_changed``; tool errors become
``ModelRetry``; the agent's default retry budget).
Agent: ``pydantic_ai.Agent(model, toolsets=[...], instructions=...)``.
Model: the ``provider:model`` string, which Pydantic AI resolves natively.

Checked with pydantic-ai-slim 2.56.0, fastmcp-slim 4.1.0 and mcp 2.3.0, which
speaks MCP protocol 2026-07-28 against the Polygraph worlds.

Recipes go in by subclassing ``MCPToolset`` and wrapping ``list_tools`` and
``direct_call_tool``. Errors that a recipe produced or annotated reach the model
as ``ToolFailed`` (shown to the model, no retry counted), so recipe notes do not
use up Pydantic AI's retry budget; untouched server errors keep the default
``ModelRetry`` path.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from bench.agent import AgentResult
from recipes.core import ToolCallResult
from recipes.mcp_compat import tool_dict, tool_from_dict

from .base import BaseAgent


def _recipe_toolset_cls(stack: Any, key: str) -> Any:
    from pydantic_ai.exceptions import ModelRetry, ToolFailed
    from pydantic_ai.mcp import MCPToolset

    class RecipeToolset(MCPToolset):  # type: ignore[misc]
        async def list_tools(self) -> Any:
            tools = await super().list_tools()
            by_name = {t.name: t for t in tools}
            shown = stack.on_tools(key, [tool_dict(t) for t in tools])
            return [
                tool_from_dict(by_name[d["name"]], d) for d in shown if d.get("name") in by_name
            ]

        async def direct_call_tool(
            self,
            name: str,
            args: dict[str, Any],
            *,
            metadata: dict[str, Any] | None = None,
            use_task: bool = False,
        ) -> Any:
            parent = super()

            async def nxt(a: dict[str, Any]) -> ToolCallResult:
                try:
                    out = await parent.direct_call_tool(
                        name, a, metadata=metadata, use_task=use_task
                    )
                except ModelRetry as exc:
                    return ToolCallResult(text=str(exc.message), is_error=True, raw=exc)
                text = out if isinstance(out, str) else json.dumps(out, default=str)
                return ToolCallResult(
                    text=text, structured=out if isinstance(out, dict) else None, raw=out
                )

            res = await stack.call(key, name, dict(args or {}), nxt)
            if res.is_error:
                if not res.changed and isinstance(res.raw, ModelRetry):
                    raise res.raw
                raise ToolFailed(message=res.text)
            if not res.changed:
                return res.raw
            if res.prefer_text or res.structured is None:
                return res.text
            return res.structured

    return RecipeToolset


class PydanticAIAgent(BaseAgent):
    framework = "pydantic-ai"
    distribution = "pydantic-ai-slim"
    requires = ("pydantic_ai", "pydantic_ai.mcp", "fastmcp")
    extra = "pydantic-ai"
    BEST_PRACTICE: dict[str, dict[str, Any]] = {
        # "toolset": {...},  kwargs for MCPToolset (e.g. tool_error_behavior, max_retries)
        # "agent": {...},    kwargs for pydantic_ai.Agent
        # "run": {...},      kwargs for agent.run (e.g. usage_limits)
    }

    def transport(self, cfg: dict[str, Any]) -> Any:
        from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

        if "command" in cfg:
            command, args, env, cwd = self.stdio_parts(cfg)
            return StdioTransport(command=command, args=args, env=env, cwd=cwd)
        return StreamableHttpTransport(cfg["url"], headers=dict(cfg.get("headers") or {}))

    def toolset(self, name: str, cfg: dict[str, Any]) -> Any:
        from pydantic_ai.mcp import MCPToolset

        cls: Any = MCPToolset if self.stack is None else _recipe_toolset_cls(self.stack, name)
        return cls(self.transport(cfg), id=name, **self.settings.get("toolset", {}))

    @staticmethod
    async def close_transports(toolsets: list[Any]) -> None:
        """Stop the server processes after a run.

        FastMCP's ``StdioTransport`` keeps the server process alive after the session
        ends (``keep_alive`` defaults to true). Closing it *after* the run changes nothing
        the agent sees; it only stops processes leaking from one test into the next.
        """
        for ts in toolsets:
            close = getattr(getattr(ts.client, "transport", None), "close", None)
            if close is not None:
                with contextlib.suppress(Exception):  # best-effort cleanup after the run
                    await close()

    async def probe(self) -> list[str]:
        names: list[str] = []
        toolsets: list[Any] = []
        try:
            for name, cfg in self.servers.items():
                ts = self.toolset(name, cfg)
                toolsets.append(ts)
                async with ts:
                    tools = await ts.list_tools()
                    names.extend(t.name for t in tools)
                    if any(t.name == "get_invoice" for t in tools):
                        await ts.direct_call_tool("get_invoice", {"invoice_id": "INV-42"})
        finally:
            await self.close_transports(toolsets)
        return sorted(names)

    async def _arun(self, instruction: str) -> AgentResult:
        from pydantic_ai import Agent

        toolsets = [self.toolset(name, cfg) for name, cfg in self.servers.items()]
        agent = Agent(
            self.model_override or f"{self.model.provider}:{self.model.name}",
            toolsets=toolsets,
            instructions=self.system_prompt,
            **self.settings.get("agent", {}),
        )
        try:
            async with agent:
                result = await agent.run(instruction, **self.settings.get("run", {}))
        finally:
            await self.close_transports(toolsets)
        # a method in Pydantic AI 1.x, a property in 2.x
        usage = result.usage() if callable(result.usage) else result.usage
        self.add_usage(usage.input_tokens or 0, usage.output_tokens or 0)
        return AgentResult(
            answer=str(result.output or ""),
            tokens_in=self._usage[0],
            tokens_out=self._usage[1],
            info={"requests": getattr(usage, "requests", None)},
        )
