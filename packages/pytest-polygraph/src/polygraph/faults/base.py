"""Building blocks shared by every fault.

A fault sees one agent ``tools/call`` at a time and decides what the server
gets (zero, one or two deliveries) and what the agent is told. Everything is
deterministic for a given seed: each fault gets its own ``random.Random``
seeded from the plan seed and the fault's identity.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..jsonrpc import Message, error_response, response
from ..protocol import tool_result
from ..results import FaultName, FaultSpec

# --------------------------------------------------------------------------- #
# The fault plan handed to a proxy (JSON file or env var)
# --------------------------------------------------------------------------- #


class FaultPlan(BaseModel):
    """What one proxy instance should break. Written by the plugin, read by the proxy."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    run_id: str = "adhoc"
    server: str = "server"
    seed: int = 0
    faults: list[FaultSpec] = Field(default_factory=list)
    timeout_s: float = 30.0
    slow_ms: int = 8000
    #: defaults per fault name from ``[tool.polygraph.faults.<name>]``
    fault_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: W3C traceparent of the run span; the proxy adds child span ids per call
    traceparent: str | None = None
    #: cassette whose recorded responses are re-served (``polygraph replay``)
    replay_cassette: str | None = None

    def for_server(self, server: str) -> list[FaultSpec]:
        return [f for f in self.faults if f.server in (server, "*", "")]


# --------------------------------------------------------------------------- #
# Calls, results, upstream
# --------------------------------------------------------------------------- #


class Upstream(Protocol):
    """The real server, as seen from a fault."""

    async def send(self, msg: Message) -> Message | None:
        """Deliver a request and wait for its response (``None`` if none came)."""
        ...


@dataclass
class ToolCall:
    request: Message
    tool: str
    arguments: dict[str, Any]
    call_index: int  # 1-based count of agent calls to this tool in the run
    modern: bool  # the client speaks the 2026-07-28 era (results carry resultType)


@dataclass
class CallResult:
    """What a fault did with one call."""

    told: Message | bytes | None  # bytes = deliberately broken wire data; None = no answer
    forwarded: int = 0
    server_response: Message | None = None
    duplicate_response: Message | None = None
    note: str | None = None
    canaries: list[str] = field(default_factory=list)


@dataclass
class FaultContext:
    plan: FaultPlan
    spec: FaultSpec
    #: tool definitions from the most recent ``tools/list`` (name -> tool object)
    known_tools: Callable[[], dict[str, dict[str, Any]]] = lambda: {}
    #: earlier server results for (tool, canonical arguments), oldest first
    history: Callable[[str, str], list[dict[str, Any]]] = lambda tool, args: []
    #: waits until the client cancels request ``id`` or ``timeout`` passes; True if cancelled
    wait_cancelled: Callable[[Any, float], Awaitable[bool]] | None = None
    #: sleep function (patched in tests)
    sleep: Callable[[float], Awaitable[None]] | None = None

    @property
    def rng(self) -> random.Random:
        if not hasattr(self, "_rng"):
            key = f"{self.plan.seed}:{self.spec.name.value}:{self.spec.server}:{self.spec.tool}"
            seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
            self._rng = random.Random(seed)  # noqa: S311 - determinism, not security
        return self._rng

    def param(self, name: str, default: Any = None) -> Any:
        if name in self.spec.params:
            return self.spec.params[name]
        return self.plan.fault_params.get(self.spec.name.value, {}).get(name, default)

    @property
    def canary(self) -> str:
        """A unique, stable marker for hijack detection, e.g. ``PG-CANARY-1a2b3c4d``."""
        key = f"canary:{self.plan.seed}:{self.plan.run_id}:{self.spec.name.value}:{self.spec.tool}"
        return "PG-CANARY-" + hashlib.sha256(key.encode()).hexdigest()[:8]

    @property
    def canary_address(self) -> str:
        return f"audit+{self.canary.lower()}@example.test"

    async def do_sleep(self, seconds: float) -> None:
        if self.sleep is not None:
            await self.sleep(seconds)
            return
        import anyio

        await anyio.sleep(seconds)


class Fault:
    """Base class. Subclasses set ``name`` and override :meth:`on_call` (and maybe more)."""

    name: ClassVar[FaultName]
    #: faults that only touch ``tools/list`` set this to False
    intercepts_calls: ClassVar[bool] = True

    def __init__(self, ctx: FaultContext) -> None:
        self.ctx = ctx
        self.spec = ctx.spec

    # -- targeting -------------------------------------------------------------
    def targets(self, tool: str) -> bool:
        return self.spec.tool in (tool, "*")

    def applies(self, call: ToolCall) -> bool:
        """True if this call is one the fault should break.

        ``on_call`` picks the first broken call; param ``times`` (default 1) says
        how many calls in a row are broken; ``times = 0`` breaks every call from
        ``on_call`` on.
        """
        if not self.intercepts_calls or not self.targets(call.tool):
            return False
        times = int(self.ctx.param("times", 1))
        start = self.spec.on_call
        if call.call_index < start:
            return False
        return times <= 0 or call.call_index < start + times

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        raise NotImplementedError

    def on_tools_list(
        self, result: dict[str, Any], *, poisoned: bool
    ) -> tuple[dict[str, Any], list[str]]:
        """Rewrite a ``tools/list`` result. Returns the result and canaries told."""
        return result, []

    # -- helpers for subclasses ----------------------------------------------
    async def forward(self, call: ToolCall, upstream: Upstream) -> CallResult:
        resp = await upstream.send(call.request)
        return CallResult(told=resp, forwarded=1, server_response=resp)

    def tool_error(self, call: ToolCall, text: str) -> Message:
        """An error the agent sees, as a tool error (default) or a JSON-RPC error."""
        if self.ctx.param("as", "tool_error") == "rpc_error":
            return error_response(call.request.id, -32000, text)
        return response(call.request.id, tool_result(text, is_error=True, modern=call.modern))


# --------------------------------------------------------------------------- #
# Result rewriting helpers
# --------------------------------------------------------------------------- #


def result_of(msg: Message | None) -> dict[str, Any] | None:
    if msg is None or not isinstance(msg.result, dict):
        return None
    return msg.result


def with_result(msg: Message, result: dict[str, Any]) -> Message:
    raw = dict(msg.raw)
    raw["result"] = result
    return Message(raw)


def rewrite_result(
    result: dict[str, Any],
    transform: Callable[[Any], Any],
    text_fallback: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Apply ``transform`` to structured content and to every JSON text block.

    Text blocks that are not JSON go through ``text_fallback`` when given.
    """
    out = copy.deepcopy(result)
    if "structuredContent" in out and out["structuredContent"] is not None:
        out["structuredContent"] = transform(out["structuredContent"])
    for item in out.get("content") or []:
        if not (isinstance(item, dict) and item.get("type") == "text"):
            continue
        text = str(item.get("text", ""))
        try:
            parsed = json.loads(text)
        except ValueError:
            if text_fallback is not None:
                item["text"] = text_fallback(text)
            continue
        indent = 2 if "\n" in text else None
        item["text"] = json.dumps(transform(parsed), indent=indent, ensure_ascii=False)
    return out
