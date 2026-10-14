"""One adapter per framework, all behind the same interface.

.. code-block:: python

    from adapters import build_agent
    agent = build_agent(polygraph.servers(), "openai:<model-id>", SYSTEM_PROMPT, "default",
                        framework="langgraph")
    result = agent.run(task)      # AgentResult(answer, tokens_in, tokens_out)

==================  =====================================  =========================
Framework           MCP client used                        Extra
==================  =====================================  =========================
``langgraph``       langchain-mcp-adapters                 ``.[langgraph]``
``openai-agents``   agents.mcp.MCPServerStdio              ``.[openai-agents]``
``pydantic-ai``     pydantic_ai.mcp.MCPToolset (FastMCP)   ``.[pydantic-ai]``
``smolagents``      smolagents.MCPClient (mcpadapt)        ``.[smolagents]``
``scripted-naive``  polygraph.testing (no LLM, dry run)    none
``scripted-careful`` polygraph.testing (no LLM, dry run)   none
==================  =====================================  =========================

Adapter modules import their framework lazily, so this package imports with
none of them installed.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from bench import models
from bench.agent import Agent

from .base import BaseAgent, NotInstalled

#: framework name -> "module:class"
ADAPTERS: dict[str, str] = {
    "langgraph": "adapters.langgraph_adapter:LangGraphAgent",
    "openai-agents": "adapters.openai_agents_adapter:OpenAIAgentsAgent",
    "pydantic-ai": "adapters.pydantic_ai_adapter:PydanticAIAgent",
    "smolagents": "adapters.smolagents_adapter:SmolagentsAgent",
    "scripted-naive": "adapters.scripted:ScriptedNaive",
    "scripted-careful": "adapters.scripted:ScriptedCareful",
}
FRAMEWORKS = ("langgraph", "openai-agents", "pydantic-ai", "smolagents")
SCRIPTED = ("scripted-naive", "scripted-careful")

__all__ = [
    "ADAPTERS",
    "FRAMEWORKS",
    "SCRIPTED",
    "BaseAgent",
    "NotInstalled",
    "adapter_class",
    "available",
    "build_agent",
]


def adapter_class(framework: str) -> type[BaseAgent]:
    try:
        target = ADAPTERS[framework]
    except KeyError:
        raise ValueError(f"unknown framework {framework!r}; known: {', '.join(ADAPTERS)}") from None
    mod_name, _, cls_name = target.partition(":")
    cls: type[BaseAgent] = getattr(importlib.import_module(mod_name), cls_name)
    return cls


def available(framework: str) -> bool:
    return adapter_class(framework).available()


def build_agent(
    servers: Mapping[str, Mapping[str, Any]],
    model: str | models.ModelSpec,
    system_prompt: str,
    config_label: str = "default",
    *,
    framework: str,
    seed: int | str = 0,
    call_timeout: float = 30.0,
) -> Agent:
    """Build the agent for ``framework`` (raises :class:`NotInstalled` if its extra is missing).

    ``system_prompt`` must be the shared :data:`bench.prompt.SYSTEM_PROMPT`; recipe
    prompt lines are appended by the adapter.
    """
    cls = adapter_class(framework)
    spec = models.parse(model)
    if spec.is_scripted != (framework in SCRIPTED):
        raise ValueError(
            f"framework {framework!r} needs "
            + ("model 'scripted'" if framework in SCRIPTED else "a real model, not 'scripted'")
        )
    from bench.prompt import SYSTEM_PROMPT

    if system_prompt != SYSTEM_PROMPT:
        raise ValueError("every framework must get the shared bench.prompt.SYSTEM_PROMPT")
    agent = cls(servers, spec, system_prompt, config_label, seed=seed, call_timeout=call_timeout)
    return agent  # type: ignore[return-value]
