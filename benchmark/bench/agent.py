"""The single interface every framework adapter implements.

.. code-block:: python

    agent = adapters.build_agent(servers, model, system_prompt, config_label)
    result = agent.run(task)          # -> AgentResult(answer, tokens_in, tokens_out)
    agent.close()

``servers`` is exactly what ``polygraph.servers()`` returns (the wrapped stdio
commands or proxy URLs), so every framework talks to the fake worlds through
the Polygraph proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Task:
    """One benchmark task.

    ``instruction`` is what an LLM agent is asked. ``scripted`` is the same task in
    the small phrase book the scripted reference agents understand (used by
    ``--dry-run``), see :mod:`polygraph.testing.agents`.
    """

    id: str
    world: str
    instruction: str
    scripted: str

    def __str__(self) -> str:
        return self.instruction


@dataclass
class AgentResult:
    """What one agent run produced. Token counts are as reported by the framework."""

    answer: str
    tokens_in: int = 0
    tokens_out: int = 0
    #: free-form facts about the run worth keeping (model turns, stop reason, ...)
    info: dict[str, Any] = field(default_factory=dict)


class AgentCrash(RuntimeError):
    """The agent itself raised. Carries the tokens spent before the crash.

    Polygraph scores a run whose agent raised as ``crash``; the task helper records
    the tokens first and then re-raises.
    """

    def __init__(self, message: str, *, tokens_in: int = 0, tokens_out: int = 0) -> None:
        super().__init__(message)
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


@runtime_checkable
class Agent(Protocol):
    framework: str
    config_label: str

    def run(self, task: Task | str) -> AgentResult: ...

    def close(self) -> None: ...


def instruction_of(task: Task | str) -> str:
    return task.instruction if isinstance(task, Task) else str(task)


def text_of(content: Any) -> str:
    """Flatten a framework's message content (str, list of blocks, ...) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list | tuple):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in (None, "text", "output_text"):
                    parts.append(str(block.get("text", "")))
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(content)
