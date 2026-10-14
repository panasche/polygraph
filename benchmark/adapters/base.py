"""Shared machinery for the framework adapters.

An adapter is a subclass of :class:`BaseAgent` that knows how to

1. connect its framework's MCP client to the servers Polygraph hands out,
2. build the framework's agent with the shared system prompt and the model, and
3. run one task and report the answer and the tokens the framework counted.

Fairness rules every adapter follows:

- ``default`` uses the framework's default settings. No timeouts, retries,
  caches or turn limits are changed. The only exception is switching off
  telemetry that would send traces to a vendor (it does not change behaviour).
- ``best-practice`` applies the adapter's ``BEST_PRACTICE`` settings, which stay
  empty until the framework's maintainers suggest some (see README).
- Recipe labels (``R1``..``R7``) install the shared :mod:`recipes` stack through
  the framework's own extension point, and append the recipes' prompt lines.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
from collections.abc import Mapping
from typing import Any, ClassVar

from bench import configs
from bench.agent import AgentCrash, AgentResult, Task, instruction_of
from bench.models import ModelSpec
from bench.prompt import build_prompt
from recipes import RecipeStack, build_stack


class NotInstalled(RuntimeError):
    """The framework's extra is not installed in this environment."""


class BaseAgent:
    #: adapter name, used in results files (``environment.framework``)
    framework: ClassVar[str] = ""
    #: the distribution whose version is reported as ``framework_version``
    distribution: ClassVar[str] = ""
    #: modules that must import for the adapter to work
    requires: ClassVar[tuple[str, ...]] = ()
    #: pip extra that installs it: ``pip install -e ".[<extra>]"``
    extra: ClassVar[str] = ""
    #: settings suggested by the framework's maintainers for the ``best-practice`` column.
    #: Keys are adapter-specific (``client``, ``agent``, ``run`` ...). Empty until they reply.
    BEST_PRACTICE: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(
        self,
        servers: Mapping[str, Mapping[str, Any]],
        model: ModelSpec,
        system_prompt: str,
        config_label: str = configs.DEFAULT,
        *,
        seed: int | str = 0,
        call_timeout: float = 30.0,
    ) -> None:
        self.ensure_available()
        self.servers = {k: dict(v) for k, v in servers.items()}
        self.model = model
        self.config = configs.resolve(config_label)
        self.config_label = self.config.label
        self.seed = seed
        self.call_timeout = call_timeout
        self.stack: RecipeStack | None = (
            build_stack(self.config.recipes, seed=seed) if self.config.recipes else None
        )
        self.system_prompt = build_prompt(
            self.stack.prompt_lines if self.stack else (), base=system_prompt
        )
        self.settings: dict[str, dict[str, Any]] = (
            {k: dict(v) for k, v in self.BEST_PRACTICE.items()} if self.config.best_practice else {}
        )
        self._usage = [0, 0]  # tokens seen so far in the current run (for crashes)
        #: tests only: a framework-native model object used instead of the real provider,
        #: so the agent loop can be exercised offline. Never set in a benchmark run.
        self.model_override: Any = None

    # ------------------------------------------------------------- discovery
    @classmethod
    def available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in cls.requires)

    @classmethod
    def ensure_available(cls) -> None:
        if not cls.available():
            raise NotInstalled(
                f'{cls.framework} is not installed here: pip install -e ".[{cls.extra}]"'
            )

    @classmethod
    def version(cls) -> str | None:
        try:
            return importlib.metadata.version(cls.distribution)
        except importlib.metadata.PackageNotFoundError:
            return None

    # ------------------------------------------------------------- running
    def run(self, task: Task | str) -> AgentResult:
        if self.stack is not None:
            self.stack.reset(self.seed)
        self._usage = [0, 0]
        try:
            return self._run(task)
        except AgentCrash:
            raise
        except Exception as exc:
            raise AgentCrash(
                f"{type(exc).__name__}: {str(exc)[:300]}",
                tokens_in=self._usage[0],
                tokens_out=self._usage[1],
            ) from exc

    def _run(self, task: Task | str) -> AgentResult:
        """Default: run the async implementation on a fresh event loop."""
        return asyncio.run(self._arun(instruction_of(task)))

    async def _arun(self, instruction: str) -> AgentResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:
        """Every run opens and closes its own MCP sessions, so there is nothing to hold."""

    # ------------------------------------------------------------- helpers
    @staticmethod
    def stdio_parts(cfg: Mapping[str, Any]) -> tuple[str, list[str], dict[str, str], str | None]:
        """``{"command": [...], "env": {...}, "cwd": ...}`` -> (command, args, env, cwd)."""
        cmd = list(cfg["command"])
        env = {str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
        # Frameworks start servers with a minimal environment; keep what Python needs.
        for var in ("SYSTEMROOT", "PATH", "PYTHONUTF8", "TEMP", "TMP"):
            if var in os.environ and var not in env:
                env[var] = os.environ[var]
        return cmd[0], cmd[1:], env, cfg.get("cwd")

    def add_usage(self, tokens_in: int, tokens_out: int) -> None:
        self._usage[0] += int(tokens_in or 0)
        self._usage[1] += int(tokens_out or 0)
