"""LangGraph, through ``langchain-mcp-adapters``.

MCP client: ``langchain_mcp_adapters.client.MultiServerMCPClient`` with
``get_tools()`` (its documented default: a new MCP session per tool call).
Agent: ``langchain.agents.create_agent`` (the LangGraph agent loop that replaced
``langgraph.prebuilt.create_react_agent``, now deprecated).
Model: ``langchain.chat_models.init_chat_model(model, model_provider=provider)``.

Checked with langgraph 1.2.15, langchain 1.4.5, langchain-mcp-adapters 0.3.2 and
mcp 1.30.0 (``langchain-mcp-adapters`` requires ``mcp<2``), which speaks MCP
protocol 2025-11-25.

Recipes go in through ``tool_interceptors`` (the adapter library's own hook). The
adapter loads the tool list once, before the run, so tool descriptions never
change mid-run for LangGraph: a rug pull cannot reach it, and R6 has nothing to do.
"""

from __future__ import annotations

from typing import Any

from bench.agent import AgentResult, text_of
from recipes.core import ToolCallResult
from recipes.mcp_compat import result_from_mcp, result_to_mcp, tool_dict

from .base import BaseAgent


class LangGraphAgent(BaseAgent):
    framework = "langgraph"
    distribution = "langgraph"
    requires = ("langgraph", "langchain.agents", "langchain_mcp_adapters")
    extra = "langgraph"
    BEST_PRACTICE: dict[str, dict[str, Any]] = {
        # "client": {...},  kwargs for MultiServerMCPClient
        # "agent": {...},   kwargs for create_agent
        # "run": {...},     the RunnableConfig for ainvoke (e.g. recursion_limit)
    }

    def connection(self, cfg: dict[str, Any]) -> dict[str, Any]:
        if "command" in cfg:
            command, args, env, cwd = self.stdio_parts(cfg)
            conn: dict[str, Any] = {
                "transport": "stdio",
                "command": command,
                "args": args,
                "env": env,
            }
            if cwd:
                conn["cwd"] = cwd
            return conn
        out: dict[str, Any] = {"transport": "streamable_http", "url": cfg["url"]}
        if cfg.get("headers"):
            out["headers"] = dict(cfg["headers"])
        return out

    def chat_model(self) -> Any:
        if self.model_override is not None:
            return self.model_override
        from langchain.chat_models import init_chat_model

        return init_chat_model(self.model.name, model_provider=self.model.provider)

    async def _interceptor(self, request: Any, handler: Any) -> Any:
        assert self.stack is not None
        from mcp.types import CallToolResult

        async def nxt(args: dict[str, Any]) -> ToolCallResult:
            res = await handler(request.override(args=args))
            if isinstance(res, CallToolResult):
                return result_from_mcp(res)
            return ToolCallResult(text=text_of(getattr(res, "content", res)), raw=res)

        out = await self.stack.call(request.server_name, request.name, request.args, nxt)
        if not out.changed and out.raw is not None:
            return out.raw
        return result_to_mcp(CallToolResult, out)

    async def client(self) -> Any:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        conns = {name: self.connection(cfg) for name, cfg in self.servers.items()}
        kwargs = dict(self.settings.get("client", {}))
        if self.stack is not None:
            kwargs["tool_interceptors"] = [*kwargs.get("tool_interceptors", []), self._interceptor]
        client = MultiServerMCPClient(conns, **kwargs)
        if self.stack is not None:  # the recipes need the full definitions (outputSchema too)
            for name in conns:
                async with client.session(name) as session:
                    listed = await session.list_tools()
                    self.stack.on_tools(name, [tool_dict(t) for t in listed.tools])
        return client

    async def probe(self) -> list[str]:
        """List tools and call ``get_invoice`` through the framework's MCP client (no model)."""
        client = await self.client()
        tools = {t.name: t for t in await client.get_tools()}
        if "get_invoice" in tools:
            await tools["get_invoice"].ainvoke({"invoice_id": "INV-42"})
        return sorted(tools)

    async def _arun(self, instruction: str) -> AgentResult:
        from langchain.agents import create_agent

        client = await self.client()
        tools = await client.get_tools()
        agent = create_agent(
            model=self.chat_model(),
            tools=tools,
            system_prompt=self.system_prompt,
            **self.settings.get("agent", {}),
        )
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": instruction}]},
            config=self.settings.get("run") or None,
        )
        messages = result.get("messages", []) if isinstance(result, dict) else []
        for m in messages:
            usage = getattr(m, "usage_metadata", None) or {}
            self.add_usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        answer = text_of(messages[-1].content) if messages else ""
        return AgentResult(
            answer=answer,
            tokens_in=self._usage[0],
            tokens_out=self._usage[1],
            info={"messages": len(messages)},
        )
