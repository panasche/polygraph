"""Fixtures for the benchmark tasks.

The framework, config label and model come from environment variables set by
``run.py`` (see :mod:`bench.settings`). Every framework gets the same system
prompt and the servers Polygraph hands out (through the proxy).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from polygraph import Polygraph

from adapters import NotInstalled, adapter_class, build_agent
from bench import settings
from bench.agent import Agent, AgentCrash, Task
from bench.prompt import SYSTEM_PROMPT


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "headline: task pinned to one headline fault")
    config.addinivalue_line("markers", "model_check: one of the three model-check faults (§7.6)")
    try:
        s = settings.current()
        cls = adapter_class(s.framework)
        cls.ensure_available()
    except (RuntimeError, ValueError, NotInstalled) as exc:
        raise pytest.UsageError(f"benchmark tasks: {exc}") from exc
    settings.export_labels(s, cls.version(), os.environ)


@pytest.fixture
def agent(polygraph: Polygraph) -> Iterator[Agent]:
    s = settings.current()
    servers = polygraph.servers()
    if polygraph.worlds:  # give the agent only its task's world (the polygraph_world marker)
        servers = {k: v for k, v in servers.items() if k in polygraph.worlds}
    built = build_agent(
        servers,
        s.model,
        SYSTEM_PROMPT,
        s.config_label,
        framework=s.framework,
        seed=polygraph.seed,
        call_timeout=s.call_timeout,
    )
    yield built
    built.close()


@pytest.fixture
def run_task(agent: Agent, polygraph: Polygraph) -> Callable[[Task], str]:
    """Run one task, record its answer and tokens, and return the answer."""

    def _run(task: Task) -> str:
        try:
            result = agent.run(task)
        except AgentCrash as exc:
            polygraph.record_tokens(exc.tokens_in, exc.tokens_out)
            raise
        polygraph.record_tokens(result.tokens_in, result.tokens_out)
        polygraph.record_answer(result.answer)
        return result.answer

    return _run
