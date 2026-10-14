"""smolagents, through its MCP support (``smolagents.MCPClient``, built on mcpadapt).

MCP client: ``smolagents.MCPClient`` with its defaults (tools are loaded once when
it connects; ``structured_output`` left unset, which means ``False`` today).
Agent: ``smolagents.ToolCallingAgent(tools=..., model=..., instructions=...)``
(default ``max_steps=20``). ``ToolCallingAgent`` is used rather than ``CodeAgent``
because it calls tools the same way the other three frameworks do (JSON tool
calls), which keeps the comparison fair.
Model: ``smolagents.OpenAIModel``; for providers other than OpenAI it points at
the provider's OpenAI-compatible endpoint.

Checked with smolagents 1.26.0, mcpadapt 0.1.20 and mcp 1.30.0 (mcpadapt 0.1.20
does not import under mcp 2.x), which speaks MCP protocol 2025-11-25.

Worth knowing: mcpadapt 0.1.20 ignores ``isError`` and returns an error result's
text to the model as if it were a normal result.

Recipes go in by giving mcpadapt a subclass of its ``SmolAgentsAdapter`` that
wraps the raw MCP call each tool makes. Tools never change after connect, so a
rug pull cannot reach smolagents and R6 has nothing to do.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

from bench.agent import AgentResult, Task, instruction_of
from bench.models import OPENAI_COMPATIBLE_BASE_URL
from recipes.core import ToolCallResult, run_sync
from recipes.mcp_compat import result_from_mcp, result_to_mcp, tool_dict

from .base import BaseAgent


def _recipe_adapter(stack: Any) -> Any:
    import mcp.types
    from mcpadapt.smolagents_adapter import SmolAgentsAdapter

    class RecipeSmolAgentsAdapter(SmolAgentsAdapter):  # type: ignore[misc]
        def adapt(self, func: Any, mcp_tool: Any) -> Any:
            stack.on_tools("*", [tool_dict(mcp_tool)])
            name = mcp_tool.name

            def wrapped(args: dict[str, Any] | None) -> Any:
                async def nxt(a: dict[str, Any]) -> ToolCallResult:
                    return result_from_mcp(func(a))

                out = run_sync(stack.call("*", name, dict(args or {}), nxt))
                return result_to_mcp(mcp.types.CallToolResult, out)

            return super().adapt(wrapped, mcp_tool)

    return RecipeSmolAgentsAdapter


class SmolagentsAgent(BaseAgent):
    framework = "smolagents"
    distribution = "smolagents"
    requires = ("smolagents", "mcpadapt")
    extra = "smolagents"
    BEST_PRACTICE: dict[str, dict[str, Any]] = {
        # "client": {...},  kwargs for MCPClient (e.g. structured_output, adapter_kwargs)
        # "agent": {...},   kwargs for ToolCallingAgent (e.g. max_steps)
    }

    def params(self, cfg: dict[str, Any]) -> Any:
        if "command" in cfg:
            from mcp import StdioServerParameters

            command, args, env, cwd = self.stdio_parts(cfg)
            return StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
        out: dict[str, Any] = {"url": cfg["url"], "transport": "streamable-http"}
        if cfg.get("headers"):
            out["headers"] = dict(cfg["headers"])
        return out

    def connect(self) -> tuple[Any, list[Any]]:
        """(something with ``disconnect()`` or ``__exit__``, tools)."""
        params = [self.params(cfg) for cfg in self.servers.values()]
        kwargs = dict(self.settings.get("client", {}))
        if self.stack is not None:
            from mcpadapt.core import MCPAdapt

            structured = bool(kwargs.pop("structured_output", False))
            adapter = _recipe_adapter(self.stack)(structured_output=structured)
            adapt = MCPAdapt(params, adapter, **kwargs.pop("adapter_kwargs", {}))
            return adapt, list(adapt.__enter__())
        from smolagents import MCPClient

        with warnings.catch_warnings():
            # the "structured_output not specified" FutureWarning: we keep the default on purpose
            warnings.simplefilter("ignore", FutureWarning)
            client = MCPClient(params, **kwargs)
        return client, list(client.get_tools())

    @staticmethod
    def disconnect(handle: Any) -> None:
        if hasattr(handle, "disconnect"):
            handle.disconnect()
        else:
            handle.__exit__(None, None, None)

    def model_obj(self) -> Any:
        if self.model_override is not None:
            return self.model_override
        from smolagents import OpenAIModel

        if self.model.provider == "openai":
            return OpenAIModel(model_id=self.model.name)
        return OpenAIModel(
            model_id=self.model.name,
            api_base=OPENAI_COMPATIBLE_BASE_URL[self.model.provider],
            api_key=self.model.api_key(),
        )

    def probe_sync(self) -> list[str]:
        handle, tools = self.connect()
        try:
            by_name = {t.name: t for t in tools}
            if "get_invoice" in by_name:
                by_name["get_invoice"](invoice_id="INV-42")
            return sorted(by_name)
        finally:
            self.disconnect(handle)

    async def probe(self) -> list[str]:
        return await asyncio.to_thread(self.probe_sync)

    def _run(self, task: Task | str) -> AgentResult:
        from smolagents import ToolCallingAgent

        handle, tools = self.connect()
        try:
            agent = ToolCallingAgent(
                tools=tools,
                model=self.model_obj(),
                instructions=self.system_prompt,
                **self.settings.get("agent", {}),
            )
            result = agent.run(instruction_of(task), return_full_result=True)
            usage = getattr(result, "token_usage", None)
            if usage is not None:
                self.add_usage(usage.input_tokens, usage.output_tokens)
            return AgentResult(
                answer=str(getattr(result, "output", "") or ""),
                tokens_in=self._usage[0],
                tokens_out=self._usage[1],
                info={"state": str(getattr(result, "state", ""))},
            )
        finally:
            self.disconnect(handle)
