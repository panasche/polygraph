"""Dry-run adapters: Polygraph's scripted reference agents instead of an LLM.

``scripted-naive`` retries on any error, follows instructions in tool text and
claims "done" without checking. ``scripted-careful`` applies the fixes by itself.
Neither calls a model, so a dry run is free, offline and deterministic for a
given seed. Tokens are reported as 0 because no model is used.

Wrapper recipes (R1, R3, R5, R6, R7, and R2's tool notes) act on the scripted
agents' MCP clients exactly as they do for real frameworks. Prompt lines (R2,
R4, R5) have no effect here: a scripted agent does not read a prompt.
"""

from __future__ import annotations

from typing import Any, ClassVar

from polygraph.testing import CarefulAgent, NaiveAgent
from polygraph.testing.agents import ScriptedAgent as _Scripted
from polygraph.testing.client import McpClient

from bench.agent import AgentResult, Task
from recipes.sync_client import RecipeClient

from .base import BaseAgent


class ScriptedAgent(BaseAgent):
    distribution = "pytest-polygraph"
    requires = ("polygraph.testing",)
    extra = "dev"
    kind: ClassVar[type[_Scripted]] = NaiveAgent

    def _make(self) -> _Scripted:
        stack = self.stack
        base = self.kind

        class Wrapped(base):  # type: ignore[valid-type,misc]
            def connect(inner) -> None:  # noqa: N805 - "inner" is the scripted agent
                for name, cfg in inner.server_configs.items():
                    if name not in inner.clients:
                        client = McpClient.connect(cfg, era=inner.era, timeout=inner.call_timeout)
                        inner.clients[name] = (
                            RecipeClient(client, stack, server=name) if stack else client
                        )
                inner.refresh_tools(force=True)

        kwargs: dict[str, Any] = {"call_timeout": self.call_timeout}
        if issubclass(base, CarefulAgent):
            kwargs["seed"] = self.seed
        return Wrapped(self.servers, **kwargs)

    async def probe(self) -> list[str]:
        agent = self._make()
        agent.connect()
        try:
            if agent.has("get_invoice"):
                agent.raw_call("get_invoice", {"invoice_id": "INV-42"})
            return sorted(agent.tools)
        finally:
            agent.close()

    def _run(self, task: Task | str) -> AgentResult:
        phrase = task.scripted if isinstance(task, Task) else str(task)
        answer = self._make().run(phrase)
        return AgentResult(answer=answer, tokens_in=0, tokens_out=0, info={"scripted": True})


class ScriptedNaive(ScriptedAgent):
    framework = "scripted-naive"
    kind = NaiveAgent


class ScriptedCareful(ScriptedAgent):
    framework = "scripted-careful"
    kind = CarefulAgent
