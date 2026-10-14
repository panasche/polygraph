"""Family 1: the tool is down or slow (timeout, slow, error, rate_limit, auth_expired)."""

from __future__ import annotations

from ..jsonrpc import REQUEST_TIMEOUT, error_response
from ..results import FaultName
from .base import CallResult, Fault, ToolCall, Upstream


class Timeout(Fault):
    """No reply for ``timeout_s`` seconds, then a JSON-RPC "request timed out" error.

    A real hung server never answers. Polygraph answers after ``timeout_s``
    (``[tool.polygraph] timeout_s``, default 30) so that a test can never hang
    forever. The request does not reach the server unless ``forward = true``.
    If the client cancels the request first (``notifications/cancelled`` or
    closing the HTTP stream) the proxy stops holding and sends nothing.
    """

    name = FaultName.TIMEOUT

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        forwarded = 0
        server_resp = None
        if self.ctx.param("forward", False):
            server_resp = await upstream.send(call.request)
            forwarded = 1
        hold = float(self.ctx.param("timeout_s", self.ctx.plan.timeout_s))
        cancelled = False
        if self.ctx.wait_cancelled is not None:
            cancelled = await self.ctx.wait_cancelled(call.request.id, hold)
        else:
            await self.ctx.do_sleep(hold)
        if cancelled:
            return CallResult(
                told=None,
                forwarded=forwarded,
                server_response=server_resp,
                note=f"held the call; the client cancelled it before {hold:g} s",
            )
        told = error_response(call.request.id, REQUEST_TIMEOUT, "Request timed out")
        return CallResult(
            told=told,
            forwarded=forwarded,
            server_response=server_resp,
            note=f"no reply for {hold:g} s, then a timeout error",
        )


class Slow(Fault):
    """The correct reply, but only after ``slow_ms`` milliseconds."""

    name = FaultName.SLOW

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        res = await self.forward(call, upstream)
        delay_ms = float(self.ctx.param("slow_ms", self.ctx.plan.slow_ms))
        await self.ctx.do_sleep(delay_ms / 1000.0)
        res.note = f"correct reply held back for {delay_ms:g} ms"
        return res


class Error(Fault):
    """A generic failure (``isError: true``), like an HTTP 500. The server never sees the call."""

    name = FaultName.ERROR

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        text = self.ctx.param(
            "message",
            f"Internal server error (HTTP 500). Something went wrong while running "
            f"{call.tool}. Please try again later.",
        )
        return CallResult(
            told=self.tool_error(call, text), note="request not delivered; agent got an error"
        )


class RateLimit(Fault):
    """ "Too many requests, retry in N s" (HTTP 429 in spirit). The server never sees the call."""

    name = FaultName.RATE_LIMIT

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        after = float(self.ctx.param("retry_after_s", 10))
        text = f"Too many requests (HTTP 429). Rate limit exceeded, retry in {after:g} s."
        return CallResult(
            told=self.tool_error(call, text),
            note=f"request not delivered; agent told to retry in {after:g} s",
        )


class AuthExpired(Fault):
    """ "Your login expired" as a tool error (a real HTTP 401 comes in v1.1)."""

    name = FaultName.AUTH_EXPIRED

    async def on_call(self, call: ToolCall, upstream: Upstream) -> CallResult:
        text = (
            "Unauthorized (HTTP 401): your login has expired. Please sign in again "
            "and retry the request."
        )
        return CallResult(
            told=self.tool_error(call, text), note="request not delivered; agent told login expired"
        )
