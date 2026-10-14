"""OpenAI Agents SDK (``openai-agents``).

MCP client: ``agents.mcp.MCPServerStdio`` / ``MCPServerStreamableHttp`` with their
defaults (``cache_tools_list=False``, so the tool list is fetched again on every
turn; ``client_session_timeout_seconds=5``; no retries).
Agent: ``agents.Agent(instructions=..., mcp_servers=[...])`` run with
``agents.Runner.run`` (default ``max_turns=10``).
Model: an OpenAI model name goes straight to the SDK (its default Responses API).
Other providers go through ``OpenAIChatCompletionsModel`` and the provider's
OpenAI-compatible endpoint, because the SDK has no native client for them.

Checked with openai-agents 0.23.1 and mcp 2.3.0, which speaks MCP protocol
2026-07-28 against the Polygraph worlds.

Tracing is switched off: by default the SDK uploads traces to OpenAI, and that
changes nothing about how the agent behaves.

Recipes go in by subclassing the server class and wrapping ``list_tools`` and
``call_tool`` (the SDK's own public methods).
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from bench.agent import AgentResult
from bench.models import OPENAI_COMPATIBLE_BASE_URL
from recipes.core import ToolCallResult
from recipes.mcp_compat import result_from_mcp, result_to_mcp, tool_dict, tool_from_dict

from .base import BaseAgent


def _with_recipes(base: type, stack: Any, key: str) -> Any:
    from mcp.types import CallToolResult

    class RecipeServer(base):  # type: ignore[valid-type,misc]
        async def list_tools(self, run_context: Any = None, agent: Any = None) -> Any:
            tools = await super().list_tools(run_context, agent)
            by_name = {t.name: t for t in tools}
            shown = stack.on_tools(key, [tool_dict(t) for t in tools])
            return [
                tool_from_dict(by_name[d["name"]], d) for d in shown if d.get("name") in by_name
            ]

        async def call_tool(
            self, tool_name: str, arguments: dict[str, Any] | None, meta: Any = None
        ) -> Any:
            parent = super()

            async def nxt(args: dict[str, Any]) -> ToolCallResult:
                return result_from_mcp(await parent.call_tool(tool_name, args, meta))

            out = await stack.call(key, tool_name, dict(arguments or {}), nxt)
            return result_to_mcp(CallToolResult, out)

    RecipeServer.__name__ = f"Recipe{base.__name__}"
    return RecipeServer


class OpenAIAgentsAgent(BaseAgent):
    framework = "openai-agents"
    distribution = "openai-agents"
    requires = ("agents", "agents.mcp")
    extra = "openai-agents"
    BEST_PRACTICE: dict[str, dict[str, Any]] = {
        # "server": {...},  kwargs for MCPServerStdio / MCPServerStreamableHttp
        # "agent": {...},   kwargs for agents.Agent
        # "run": {...},     kwargs for Runner.run (e.g. max_turns)
    }

    def server(self, name: str, cfg: dict[str, Any]) -> Any:
        from agents.mcp import MCPServerStdio, MCPServerStreamableHttp

        kwargs = {"name": name, **self.settings.get("server", {})}
        if "command" in cfg:
            command, args, env, cwd = self.stdio_parts(cfg)
            params: dict[str, Any] = {"command": command, "args": args, "env": env}
            if cwd:
                params["cwd"] = cwd
            cls: type = MCPServerStdio
        else:
            params = {"url": cfg["url"]}
            if cfg.get("headers"):
                params["headers"] = dict(cfg["headers"])
            cls = MCPServerStreamableHttp
        if self.stack is not None:
            cls = _with_recipes(cls, self.stack, name)
        return cls(params=params, **kwargs)

    def model_arg(self) -> Any:
        if self.model_override is not None:
            return self.model_override
        if self.model.provider == "openai":
            return self.model.name
        from agents import OpenAIChatCompletionsModel
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=OPENAI_COMPATIBLE_BASE_URL[self.model.provider], api_key=self.model.api_key()
        )
        return OpenAIChatCompletionsModel(model=self.model.name, openai_client=client)

    async def probe(self) -> list[str]:
        names: list[str] = []
        async with AsyncExitStack() as es:
            for name, cfg in self.servers.items():
                srv = await es.enter_async_context(self.server(name, cfg))
                tools = await srv.list_tools()
                names.extend(t.name for t in tools)
                if any(t.name == "get_invoice" for t in tools):
                    await srv.call_tool("get_invoice", {"invoice_id": "INV-42"})
        return sorted(names)

    async def _arun(self, instruction: str) -> AgentResult:
        from agents import Agent, Runner, set_tracing_disabled

        set_tracing_disabled(True)
        async with AsyncExitStack() as es:
            servers = [
                await es.enter_async_context(self.server(name, cfg))
                for name, cfg in self.servers.items()
            ]
            agent = Agent(
                name="assistant",
                instructions=self.system_prompt,
                mcp_servers=servers,
                model=self.model_arg(),
                **self.settings.get("agent", {}),
            )
            try:
                result = await Runner.run(agent, instruction, **self.settings.get("run", {}))
            except Exception as exc:
                usage = getattr(
                    getattr(getattr(exc, "run_data", None), "context_wrapper", None), "usage", None
                )
                if usage is not None:
                    self.add_usage(usage.input_tokens, usage.output_tokens)
                raise
            usage = result.context_wrapper.usage
            self.add_usage(usage.input_tokens, usage.output_tokens)
            return AgentResult(
                answer=str(result.final_output or ""),
                tokens_in=self._usage[0],
                tokens_out=self._usage[1],
                info={"requests": usage.requests},
            )
