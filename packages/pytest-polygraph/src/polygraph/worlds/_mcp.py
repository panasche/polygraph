"""A small, dependency-free, dual-era MCP server used by the fake worlds.

Why not the official SDK? The worlds are spawned once per test run (often
dozens of times per suite), so start-up time matters: this server starts in
about a third of the time. It also lets the worlds behave exactly the same
under both protocol eras. The test suite checks it against the official
``mcp`` client for compatibility.

Supports:
- stdio (newline-delimited JSON) and Streamable HTTP (JSON or SSE responses);
- legacy clients (``initialize``, ``Mcp-Session-Id``, ``GET`` stream, ``DELETE``);
- modern 2026-07-28 clients (``_meta`` version on each request, ``server/discover``,
  ``subscriptions/listen``, ``resultType``, ``ttlMs``/``cacheScope``,
  ``MCP-Protocol-Version``/``Mcp-Method``/``Mcp-Name`` header checks).
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..jsonrpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    Message,
    ParseError,
    encode_sse_message,
    error_response,
    notification,
    response,
)
from ..protocol import (
    HEADER_METHOD,
    HEADER_MISMATCH,
    HEADER_NAME,
    HEADER_PROTOCOL_VERSION,
    HEADER_SESSION_ID,
    KNOWN_VERSIONS,
    LATEST_LEGACY_VERSION,
    LEGACY_VERSIONS,
    META_SERVER_INFO,
    META_SUBSCRIPTION_ID,
    MODERN_VERSIONS,
    SUBSCRIPTION_ACK,
    UNSUPPORTED_PROTOCOL_VERSION,
    decode_header_value,
    is_modern,
    request_version,
)


@dataclass
class ToolOutput:
    text: str
    structured: Any = None
    is_error: bool = False

    @classmethod
    def json(cls, data: Any) -> ToolOutput:
        return cls(text=json.dumps(data, indent=2, ensure_ascii=False), structured=data)

    @classmethod
    def error(cls, text: str) -> ToolOutput:
        return cls(text=text, is_error=True)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], ToolOutput]
    annotations: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    title: str | None = None

    def definition(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.title:
            d["title"] = self.title
        if self.annotations is not None:
            d["annotations"] = self.annotations
        if self.output_schema is not None:
            d["outputSchema"] = self.output_schema
        return d


@dataclass
class Session:
    version: str | None = None
    initialized: bool = False
    subscriptions: list[Any] = field(default_factory=list)


class McpServer:
    def __init__(self, name: str, version: str = "1.0.0", instructions: str | None = None) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools: dict[str, Tool] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- tools
    def tool(
        self,
        name: str,
        *,
        description: str,
        input_schema: dict[str, Any],
        annotations: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> Callable[[Callable[[dict[str, Any]], ToolOutput]], Callable[[dict[str, Any]], ToolOutput]]:
        def deco(
            fn: Callable[[dict[str, Any]], ToolOutput],
        ) -> Callable[[dict[str, Any]], ToolOutput]:
            self.tools[name] = Tool(
                name, description, input_schema, fn, annotations, output_schema, title
            )
            return fn

        return deco

    @property
    def server_info(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}

    def capabilities(self) -> dict[str, Any]:
        return {"tools": {"listChanged": False}}

    # ------------------------------------------------------------- handling
    def handle(self, msg: Message, session: Session) -> Message | None:
        """Answer one message. Returns ``None`` for notifications and open streams."""
        if not msg.is_request:
            return None
        method = msg.method or ""
        version = request_version(msg)
        modern = version is not None and method != "initialize"
        if modern and version not in KNOWN_VERSIONS:
            return error_response(
                msg.id,
                UNSUPPORTED_PROTOCOL_VERSION,
                "Unsupported protocol version",
                {"supported": list(MODERN_VERSIONS + LEGACY_VERSIONS[::-1]), "requested": version},
            )
        if modern and not is_modern(version):
            modern = False
        if not modern and session.version and is_modern(session.version):  # pragma: no cover
            modern = True

        def ok(result: dict[str, Any], cacheable: bool = False) -> Message:
            if modern:
                result = dict(result)
                result["resultType"] = "complete"
                meta = dict(result.get("_meta") or {})
                meta[META_SERVER_INFO] = self.server_info
                result["_meta"] = meta
                if cacheable:
                    result.setdefault("ttlMs", 60_000)
                    result.setdefault("cacheScope", "public")
            return response(msg.id, result)

        if method == "initialize":
            requested = msg.params.get("protocolVersion")
            negotiated = requested if requested in LEGACY_VERSIONS else LATEST_LEGACY_VERSION
            session.version = negotiated
            session.initialized = True
            result: dict[str, Any] = {
                "protocolVersion": negotiated,
                "capabilities": self.capabilities(),
                "serverInfo": self.server_info,
            }
            if self.instructions:
                result["instructions"] = self.instructions
            return response(msg.id, result)
        if method == "ping":
            return ok({})
        if method == "server/discover":
            result = {
                "supportedVersions": list(MODERN_VERSIONS + LEGACY_VERSIONS[::-1]),
                "capabilities": self.capabilities(),
                "_meta": {META_SERVER_INFO: self.server_info},
            }
            if self.instructions:
                result["instructions"] = self.instructions
            modern = True
            return ok(result, cacheable=True)
        if method == "tools/list":
            return ok({"tools": [t.definition() for t in self.tools.values()]}, cacheable=True)
        if method in ("resources/list", "prompts/list", "resources/templates/list"):
            key = {"resources/list": "resources", "prompts/list": "prompts"}.get(
                method, "resourceTemplates"
            )
            return ok({key: []}, cacheable=True)
        if method == "logging/setLevel":
            return ok({})
        if method == "tools/call":
            name = msg.params.get("name")
            tool = self.tools.get(name) if isinstance(name, str) else None
            if tool is None:
                return error_response(msg.id, INVALID_PARAMS, f"Unknown tool: {name}")
            args = msg.params.get("arguments") or {}
            if not isinstance(args, dict):
                return error_response(msg.id, INVALID_PARAMS, "arguments must be an object")
            try:
                with self._lock:
                    out = tool.handler(args)
            except Exception as exc:  # a tool bug becomes a tool error, like real servers
                out = ToolOutput.error(f"{type(exc).__name__}: {exc}")
            result = {"content": [{"type": "text", "text": out.text}], "isError": out.is_error}
            if out.structured is not None and not out.is_error:
                result["structuredContent"] = out.structured
            return ok(result)
        return error_response(msg.id, METHOD_NOT_FOUND, f"Method not found: {method}")

    def ack(self, msg: Message) -> Message:
        wanted = msg.params.get("notifications") or {}
        agreed = {k: v for k, v in wanted.items() if k == "toolsListChanged"}
        return notification(
            SUBSCRIPTION_ACK, {"_meta": {META_SUBSCRIPTION_ID: msg.id}, "notifications": agreed}
        )

    # ---------------------------------------------------------------- stdio
    def run_stdio(self) -> None:
        """Serve on stdin/stdout until stdin closes. Synchronous on purpose (fast start)."""
        session = Session()
        stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = Message.parse(line)
            except ParseError:
                out = error_response(None, -32700, "Parse error")
                stdout.write(out.to_line())
                stdout.flush()
                continue
            if msg.is_request and msg.method == "subscriptions/listen":
                session.subscriptions.append(msg.id)
                stdout.write(self.ack(msg).to_line())
                stdout.flush()
                continue
            reply = self.handle(msg, session)
            if reply is not None:
                stdout.write(reply.to_line())
                stdout.flush()

    # ----------------------------------------------------------------- HTTP
    def asgi_app(self, *, sse: bool = False, path: str = "/mcp") -> Callable[..., Any]:
        return _HttpApp(self, sse=sse, path=path)

    def serve_http(self, host: str = "127.0.0.1", port: int = 8000, *, sse: bool = False) -> None:
        import uvicorn

        uvicorn.run(self.asgi_app(sse=sse), host=host, port=port, log_level="warning")


class _HttpApp:
    def __init__(self, server: McpServer, *, sse: bool, path: str) -> None:
        self.server = server
        self.sse = sse
        self.path = path
        self.sessions: dict[str, Session] = {}

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        if scope["path"].rstrip("/") != self.path:
            await _respond(send, 404, b"not found", "text/plain")
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        method = scope["method"]
        sid = headers.get(HEADER_SESSION_ID)
        if method == "GET":
            if sid and sid in self.sessions:
                await self._keepalive_stream(send, receive, [])
            else:
                await _respond(send, 405, b"", "text/plain")
            return
        if method == "DELETE":
            if sid and self.sessions.pop(sid, None) is not None:
                await _respond(send, 200, b"", "text/plain")
            else:
                await _respond(send, 405, b"", "text/plain")
            return
        if method != "POST":
            await _respond(send, 405, b"", "text/plain")
            return
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        try:
            msg = Message.parse(body)
        except ParseError:
            await self._json(send, 400, error_response(None, -32700, "Parse error"))
            return
        if not msg.is_request:
            await _respond(send, 202, b"", "text/plain")
            return

        version = request_version(msg)
        extra_headers: list[tuple[bytes, bytes]] = []
        if version is not None and msg.method != "initialize":
            problem = _check_headers(headers, msg, version)
            if problem:
                await self._json(send, 400, error_response(msg.id, HEADER_MISMATCH, problem))
                return
            session = Session(version=version)
        elif msg.method == "initialize":
            sid = uuid.uuid4().hex
            session = Session()
            self.sessions[sid] = session
            extra_headers.append((HEADER_SESSION_ID.encode(), sid.encode()))
        elif sid and sid in self.sessions:
            session = self.sessions[sid]
        elif sid:
            await self._json(send, 404, error_response(msg.id, INVALID_REQUEST, "Unknown session"))
            return
        else:
            await self._json(
                send, 400, error_response(msg.id, INVALID_REQUEST, "Missing Mcp-Session-Id")
            )
            return

        if msg.method == "subscriptions/listen":
            await self._keepalive_stream(send, receive, [self.server.ack(msg)])
            return
        reply = self.server.handle(msg, session)
        assert reply is not None
        if reply.error is not None and reply.error.get("code") == UNSUPPORTED_PROTOCOL_VERSION:
            await self._json(send, 400, reply)
            return
        if self.sse:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream"), *extra_headers],
                }
            )
            await send({"type": "http.response.body", "body": encode_sse_message(reply)})
            return
        await self._json(send, 200, reply, extra_headers)

    async def _json(
        self, send: Any, status: int, msg: Message, extra: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        await _respond(send, status, msg.to_json().encode(), "application/json", extra)

    async def _keepalive_stream(self, send: Any, receive: Any, first: list[Message]) -> None:
        import anyio

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                ],
            }
        )
        for m in first:
            await send(
                {"type": "http.response.body", "body": encode_sse_message(m), "more_body": True}
            )

        async def pings() -> None:
            while True:
                await anyio.sleep(10)
                await send({"type": "http.response.body", "body": b":\n\n", "more_body": True})

        async with anyio.create_task_group() as tg:
            tg.start_soon(pings)
            while True:
                m = await receive()
                if m["type"] == "http.disconnect":
                    break
            tg.cancel_scope.cancel()


def _check_headers(headers: dict[str, str], msg: Message, version: str) -> str | None:
    hv = headers.get(HEADER_PROTOCOL_VERSION)
    if hv is None:
        return "Header mismatch: MCP-Protocol-Version header is missing"
    if hv != version:
        return f"Header mismatch: MCP-Protocol-Version {hv!r} does not match body {version!r}"
    method = headers.get(HEADER_METHOD)
    if method is not None and method != msg.method:
        return f"Header mismatch: Mcp-Method {method!r} does not match body {msg.method!r}"
    if msg.method == "tools/call":
        name = headers.get(HEADER_NAME)
        if name is not None and decode_header_value(name) != msg.params.get("name"):
            return "Header mismatch: Mcp-Name does not match body"
    return None


async def _respond(
    send: Any, status: int, body: bytes, ctype: str, extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    headers = [(b"content-type", ctype.encode())] + (extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
