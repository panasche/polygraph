"""Framework-neutral plumbing for the fix recipes.

Every adapter funnels MCP tool traffic through a :class:`RecipeStack` when a
config label asks for recipes. The stack sees two things:

- **tool lists** (``on_tools``): every time the framework lists a server's tools,
  as plain MCP tool dicts (``name``, ``description``, ``inputSchema``,
  ``outputSchema``, ``annotations``);
- **tool calls** (``call``): every call the model makes, as ``(tool, args)`` with
  a ``next`` function that performs the real MCP call and returns a
  :class:`ToolCallResult`.

Recipes are small classes with an optional prompt line. They compose like
middleware; the order is fixed (see :data:`ORDER`), whatever order the label
lists them in, so ``R2+R5`` and ``R5+R2`` behave the same.

With no recipes (the ``default`` config) adapters do not install any hook at
all, so the framework runs exactly as shipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from typing import Any

from polygraph.planner import resolve_hints


@dataclass
class ToolCallResult:
    """One tool result, the same shape for every framework.

    ``raw`` keeps the framework's own result object so an adapter can hand it back
    unchanged when no recipe touched it.
    """

    text: str = ""
    structured: Any = None
    is_error: bool = False
    raw: Any = None
    #: set by a recipe that changed the result (adapters then rebuild it)
    changed: bool = False
    #: the text carries something the model must see (R2 notes, R5 markers): adapters that
    #: would otherwise hand the model the structured content give it the text instead
    prefer_text: bool = False

    def data(self) -> Any:
        if self.structured is not None:
            return self.structured
        try:
            return json.loads(self.text)
        except (TypeError, ValueError):
            return None

    def replace(self, **kw: Any) -> ToolCallResult:
        out = ToolCallResult(
            text=kw.get("text", self.text),
            structured=kw.get("structured", self.structured),
            is_error=kw.get("is_error", self.is_error),
            raw=self.raw,
            changed=True,
            prefer_text=kw.get("prefer_text", self.prefer_text),
        )
        return out


Next = Callable[[dict[str, Any]], Awaitable[ToolCallResult]]


@dataclass
class CallContext:
    server: str
    tool: str
    definition: dict[str, Any] | None
    state: RunState

    @property
    def read_only(self) -> bool:
        return is_read_only(self.definition or {"name": self.tool})

    @property
    def input_properties(self) -> dict[str, Any]:
        schema = (self.definition or {}).get("inputSchema") or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        return props if isinstance(props, dict) else {}


@dataclass
class RunState:
    """Per-agent-run memory shared by the recipes (pins, call history, a seed)."""

    seed: str = "0"
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)  # tool -> pinned def
    pins: dict[str, str] = field(default_factory=dict)  # tool -> fingerprint
    untrusted: set[str] = field(default_factory=set)
    failures: dict[str, int] = field(default_factory=dict)  # call key -> consecutive errors
    notes: list[str] = field(default_factory=list)


def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine from synchronous code, even if this thread already runs a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # handed back to the caller below
            box["error"] = exc

    t = threading.Thread(target=work, name="recipes-run-sync", daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def is_read_only(tool: dict[str, Any]) -> bool:
    """Read-only per the tool's label, else a guess from its name (Polygraph's rules)."""
    return bool(resolve_hints("*", tool).read_only.value)


def fingerprint(tool: dict[str, Any]) -> str:
    blob = json.dumps(
        [tool.get("description"), tool.get("inputSchema")], sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def call_key(tool: str, args: dict[str, Any]) -> str:
    clean = {k: v for k, v in args.items() if k != "idempotency_key"}
    return tool + ":" + json.dumps(clean, sort_keys=True, default=str)


class Recipe:
    """Base class: override what you need."""

    label: str = ""
    title: str = ""
    #: one line added to the shared system prompt (same text for every framework)
    prompt_line: str | None = None

    def on_tools(
        self, server: str, tools: list[dict[str, Any]], state: RunState
    ) -> list[dict[str, Any]]:
        return tools

    async def call(self, ctx: CallContext, args: dict[str, Any], nxt: Next) -> ToolCallResult:
        return await nxt(args)


class RecipeStack:
    """The recipes for one agent, composed in the fixed order."""

    def __init__(self, recipes: Iterable[Recipe], *, seed: str | int = 0) -> None:
        from .fixes import ORDER

        rank = {label: i for i, label in enumerate(ORDER)}
        self.recipes = sorted(recipes, key=lambda r: rank.get(r.label, len(rank)))
        self.state = RunState(seed=str(seed))

    def __bool__(self) -> bool:
        return bool(self.recipes)

    @property
    def labels(self) -> list[str]:
        return [r.label for r in self.recipes]

    @property
    def prompt_lines(self) -> list[str]:
        return [r.prompt_line for r in self.recipes if r.prompt_line]

    def reset(self, seed: str | int | None = None) -> None:
        """Forget per-run memory (call this at the start of each task)."""
        self.state = RunState(seed=str(seed) if seed is not None else self.state.seed)

    def on_tools(self, server: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = [dict(t) for t in tools]
        for recipe in self.recipes:
            out = recipe.on_tools(server, out, self.state)
        for t in out:  # what the model is shown is what the recipes check against
            self.state.definitions[str(t.get("name"))] = t
        return out

    def definition(self, tool: str) -> dict[str, Any] | None:
        return self.state.definitions.get(tool)

    async def call(self, server: str, tool: str, args: dict[str, Any], nxt: Next) -> ToolCallResult:
        ctx = CallContext(
            server=server, tool=tool, definition=self.definition(tool), state=self.state
        )

        def link(i: int) -> Next:
            if i >= len(self.recipes):
                return nxt
            recipe = self.recipes[i]
            inner = link(i + 1)

            async def step(a: dict[str, Any]) -> ToolCallResult:
                return await recipe.call(ctx, a, inner)

            return step

        return await link(0)(dict(args or {}))
