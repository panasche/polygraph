"""Family 2: the tool did something different from what it said.

``lost_response``: the server does the work, the agent gets an error.
``duplicate_delivery``: the server gets the request twice, the agent sees one answer.
"""

from __future__ import annotations

import uuid

from ..jsonrpc import CONNECTION_LOST, Message, error_response
from ..results import FaultName
from .base import CallResult, Fault, ToolCall, Upstream


class LostResponse(Fault):
    """Forward the call; record the real result in *happened*; tell the agent it failed.

    By default the agent sees a tool error that looks like a gateway timeout.
    With ``as = "rpc_error"`` it gets a JSON-RPC "connection closed" error
    instead, which is closer to a broken HTTP response stream.
    """

    name = FaultName.LOST_RESPONSE

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        resp = await upstream.send(call.request)
        if self.ctx.param("as", "tool_error") == "rpc_error":
            told: Message = error_response(
                call.request.id, CONNECTION_LOST, "Connection closed before the response arrived"
            )
        else:
            told = self.tool_error(
                call, "Gateway timeout (HTTP 504): no response from the upstream service."
            )
        return CallResult(
            told=told,
            forwarded=1,
            server_response=resp,
            note="server ran the call; result withheld from agent (lost_response)",
        )


class DuplicateDelivery(Fault):
    """Send the request to the server twice (the second with a fresh id).

    The agent gets the first result.
    """

    name = FaultName.DUPLICATE_DELIVERY

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        first = await upstream.send(call.request)
        dup = call.request.copy()
        dup.raw["id"] = f"pg-dup-{uuid.uuid4().hex[:12]}"
        second = await upstream.send(dup)
        return CallResult(
            told=first,
            forwarded=2,
            server_response=first,
            duplicate_response=second,
            note="request delivered to the server twice (duplicate_delivery)",
        )
