"""Streamable HTTP transport: a small HTTP server in front of the real one.

    agent  --HTTP-->  polygraph proxy (this ASGI app)  --HTTP-->  real server

Handles both eras:

- legacy (2025-11-25 and earlier): ``initialize`` and ``Mcp-Session-Id``, the
  standalone ``GET`` SSE stream, ``DELETE`` to end a session;
- modern (2026-07-28): stateless POSTs with ``MCP-Protocol-Version`` and the
  ``_meta`` version, ``server/discover``, the long-lived
  ``subscriptions/listen`` SSE stream.

Responses may be plain JSON or SSE; the proxy answers in the same shape as the
upstream unless it has extra notifications to deliver (then SSE, when the client
accepts it). Requests that do not parse, notifications, responses and other
HTTP methods are passed through untouched.

It is a raw ASGI app (no framework routing needed) so it can hold a task group
for the whole life of a streamed response.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from ..faults import FaultPlan
from ..jsonrpc import SSE_KEEPALIVE, Message, ParseError, SseParser, encode_sse_message, id_key
from ..protocol import HEADER_PROTOCOL_VERSION, HEADER_SESSION_ID
from .core import ProxyEngine, Reply

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

HOP_BY_HOP = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "accept-encoding",
}
DROP_FROM_UPSTREAM = HOP_BY_HOP | {"content-type", "content-encoding", "date", "server"}


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k not in HOP_BY_HOP}


def _response_headers(upstream: httpx.Headers | None) -> list[tuple[bytes, bytes]]:
    if upstream is None:
        return []
    return [
        (k.encode("latin-1"), v.encode("latin-1"))
        for k, v in upstream.multi_items()
        if k.lower() not in DROP_FROM_UPSTREAM
    ]


class _Call:
    """One client POST: an Upstream for the engine plus a queue of things to send back."""

    def __init__(
        self,
        proxy: HttpProxy,
        headers: dict[str, str],
        primary: Message,
        queue: MemoryObjectSendStream[tuple[str, Any]],
    ) -> None:
        self.proxy = proxy
        self.headers = headers
        self.primary_key = id_key(primary.id)
        self.queue = queue
        self.response_headers: httpx.Headers | None = None
        self.http_status: int | None = None

    async def emit(self, msg: Message) -> None:
        try:
            await self.queue.send(("event", msg))
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            raise

    async def send(self, msg: Message) -> Message | None:
        primary = id_key(msg.id) == self.primary_key
        client = self.proxy.client
        headers = _forward_headers(self.headers)
        headers["content-type"] = "application/json"
        try:
            async with client.stream(
                "POST", self.proxy.upstream_url, content=msg.to_json().encode(), headers=headers
            ) as r:
                if primary:
                    self.response_headers = r.headers
                if r.status_code >= 300 or r.status_code == 202:
                    body = await r.aread()
                    if primary:
                        self.http_status = r.status_code
                        await self.queue.send(("http", (r.status_code, r.headers, body)))
                    try:
                        return Message.parse(body)
                    except ParseError:
                        return None
                ctype = r.headers.get("content-type", "")
                if "text/event-stream" not in ctype:
                    body = await r.aread()
                    try:
                        return Message.parse(body)
                    except ParseError:
                        return None
                parser = SseParser()
                async for chunk in r.aiter_bytes():
                    for ev in parser.feed(chunk):
                        try:
                            m = Message.parse(ev.data)
                        except ParseError:
                            continue
                        if m.is_response and id_key(m.id) == id_key(msg.id):
                            return m
                        if primary:
                            await self.emit(m)
                return None
        except httpx.HTTPError:
            if primary:
                self.http_status = 502
            return None


class HttpProxy:
    """ASGI app. ``HttpProxy(plan, ledger, upstream_url)`` and serve it with uvicorn."""

    def __init__(
        self,
        plan: FaultPlan,
        ledger_path: str | None,
        upstream_url: str,
        *,
        server_name: str | None = None,
        path: str = "/mcp",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.engine = ProxyEngine(plan, ledger_path, server=server_name)
        self.upstream_url = upstream_url
        self.path = path
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.legacy_pending: dict[str | None, list[Message]] = {}
        self.legacy_streams: dict[str | None, list[MemoryObjectSendStream[Message]]] = {}
        self._rug_pull_planned = any(f.mode == "rug_pull" for f in self.engine.poison_faults)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=None),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- ASGI
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":  # pragma: no cover
            return
        path = scope.get("path", "")
        if path.rstrip("/") != self.path.rstrip("/"):
            await _plain(send, 404, b"not found")
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        method = scope["method"]
        if method == "POST":
            body = await _read_body(receive)
            await self._post(headers, body, receive, send)
        elif method == "GET":
            await self._get(headers, receive, send)
        else:
            body = await _read_body(receive)
            if method == "DELETE":
                sid = headers.get(HEADER_SESSION_ID)
                self.legacy_pending.pop(sid, None)
            await self._passthrough(method, headers, body, send)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _passthrough(
        self, method: str, headers: dict[str, str], body: bytes, send: Send
    ) -> None:
        try:
            async with self.client.stream(
                method, self.upstream_url, content=body or None, headers=_forward_headers(headers)
            ) as r:
                out = _response_headers(r.headers)
                ctype = r.headers.get("content-type")
                if ctype:
                    out.append((b"content-type", ctype.encode("latin-1")))
                await send({"type": "http.response.start", "status": r.status_code, "headers": out})
                async for chunk in r.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        except httpx.HTTPError:
            await _plain(send, 502, b"upstream unreachable")

    # ---------------------------------------------------------------- POST
    async def _post(
        self, headers: dict[str, str], body: bytes, receive: Receive, send: Send
    ) -> None:
        try:
            msg = Message.parse(body)
        except ParseError:
            await self._passthrough("POST", headers, body, send)
            return
        if not msg.is_request:
            if msg.is_notification:
                self.engine.on_client_notification(msg)
            await self._passthrough("POST", headers, body, send)
            return

        session = headers.get(HEADER_SESSION_ID)
        header_version = headers.get(HEADER_PROTOCOL_VERSION)
        accepts_sse = "text/event-stream" in headers.get("accept", "")
        tx, rx = anyio.create_memory_object_stream[tuple[str, Any]](256)
        call = _Call(self, headers, msg, tx)

        async def runner() -> None:
            reply = await self.engine.handle_request(
                msg, call, session=session, header_version=header_version, emit=call.emit
            )
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await tx.send(("final", reply))

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner)
            tg.start_soon(self._watch_disconnect, receive, msg, tg)
            await self._respond(rx, call, session, accepts_sse, msg, send)
            tg.cancel_scope.cancel()
        tx.close()

    async def _watch_disconnect(
        self, receive: Receive, msg: Message, tg: anyio.abc.TaskGroup
    ) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self.engine.cancel(msg.id)
                tg.cancel_scope.cancel()
                return

    async def _respond(
        self,
        rx: MemoryObjectReceiveStream[tuple[str, Any]],
        call: _Call,
        session: str | None,
        accepts_sse: bool,
        msg: Message,
        send: Send,
    ) -> None:
        kind, value = await rx.receive()
        if kind == "http":
            status, up_headers, body = value
            out = _response_headers(up_headers)
            ctype = up_headers.get("content-type")
            if ctype:
                out.append((b"content-type", ctype.encode("latin-1")))
            await send({"type": "http.response.start", "status": status, "headers": out})
            await send({"type": "http.response.body", "body": body, "more_body": False})
            if kind == "http":
                # Let the engine finish recording; drain the final item quietly.
                with anyio.move_on_after(5):
                    while True:
                        k, _ = await rx.receive()
                        if k == "final":
                            break
            return

        pending = self.legacy_pending.get(session) or []
        if kind == "final" and not ((pending or value.before) and accepts_sse):
            reply: Reply = value
            self._after(reply, session, call, inline=False)
            await self._send_json(reply.message, call, send)
            return

        # Stream: SSE response.
        headers = _response_headers(call.response_headers)
        headers += [
            (b"content-type", b"text/event-stream"),
            (b"cache-control", b"no-cache"),
            (b"x-accel-buffering", b"no"),
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for note in self.legacy_pending.pop(session, []):
            await _sse(send, encode_sse_message(note))
        while True:
            if kind == "event":
                await _sse(send, encode_sse_message(value))
            elif kind == "final":
                reply = value
                for note in self._after(reply, session, call, inline=True):
                    await _sse(send, encode_sse_message(note))
                if isinstance(reply.message, Message):
                    await _sse(send, encode_sse_message(reply.message))
                elif isinstance(reply.message, bytes):
                    await _sse(send, b"event: message\ndata: " + reply.message + b"\n\n")
                break
            kind, value = await rx.receive()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _after(
        self, reply: Reply, session: str | None, call: _Call, *, inline: bool
    ) -> list[Message]:
        """Bind a new legacy session; route notifications that must reach the session.

        With ``inline`` they go on this response's SSE stream, just before the reply.
        Otherwise they go to the session's GET stream, or wait for its next SSE response.
        """
        if reply.negotiated_version and call.response_headers is not None:
            sid = call.response_headers.get(HEADER_SESSION_ID)
            if sid:
                self.engine.session_versions[sid] = reply.negotiated_version
        if inline:
            return list(reply.before)
        for note in reply.before:
            streams = self.legacy_streams.get(session) or []
            delivered = False
            for s in list(streams):
                try:
                    s.send_nowait(note)
                    delivered = True
                except (anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
                    streams.remove(s)
            if not delivered:
                self.legacy_pending.setdefault(session, []).append(note)
        return []

    async def _send_json(self, message: Message | bytes | None, call: _Call, send: Send) -> None:
        headers = _response_headers(call.response_headers)
        headers.append((b"content-type", b"application/json"))
        if message is None:
            status = 504 if call.http_status is None else call.http_status
            body = b""
        elif isinstance(message, bytes):
            status, body = 200, message
        else:
            status = call.http_status if call.http_status and call.http_status >= 400 else 200
            body = message.to_json().encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    # ---------------------------------------------------------------- GET
    async def _get(self, headers: dict[str, str], receive: Receive, send: Send) -> None:
        """Legacy standalone SSE stream: upstream events plus injected list_changed."""
        session = headers.get(HEADER_SESSION_ID)
        tx, rx = anyio.create_memory_object_stream[Message](64)
        out_tx, out_rx = anyio.create_memory_object_stream[bytes](256)
        self.legacy_streams.setdefault(session, []).append(tx)
        started = False

        async def upstream_events() -> None:
            nonlocal started
            try:
                async with self.client.stream(
                    "GET", self.upstream_url, headers=_forward_headers(headers)
                ) as r:
                    if r.status_code != 200:
                        if not self._rug_pull_planned:
                            body = await r.aread()
                            await out_tx.send(
                                b"STATUS:" + str(r.status_code).encode() + b":" + body
                            )
                            return
                        await out_tx.send(b"")  # serve our own stream: start it now
                        return  # ... with injected notifications only
                    # The upstream accepted the stream: start ours now, as it did, so
                    # the client is not left waiting for headers until the first event.
                    await out_tx.send(b"")
                    parser = SseParser()
                    async for chunk in r.aiter_bytes():
                        for ev in parser.feed(chunk):
                            await out_tx.send(
                                b"event: message\ndata: " + ev.data.encode() + b"\n\n"
                            )
            except httpx.HTTPError:
                return

        async def injected() -> None:
            for note in self.legacy_pending.pop(session, []):
                await out_tx.send(encode_sse_message(note))
            async for note in rx:
                await out_tx.send(encode_sse_message(note))

        async def keepalive() -> None:
            while True:
                await anyio.sleep(15)
                await out_tx.send(SSE_KEEPALIVE)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(upstream_events)
                tg.start_soon(injected)
                tg.start_soon(keepalive)
                tg.start_soon(self._watch_get_disconnect, receive, tg)
                async for chunk in out_rx:
                    if chunk.startswith(b"STATUS:"):
                        _, code, body = chunk.split(b":", 2)
                        await _plain(send, int(code), body)
                        tg.cancel_scope.cancel()
                        return
                    if not started:
                        started = True
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
                    if chunk:
                        await _sse(send, chunk)
        finally:
            streams = self.legacy_streams.get(session) or []
            if tx in streams:
                streams.remove(tx)
            if started:
                with contextlib.suppress(Exception):  # client already gone
                    await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _watch_get_disconnect(self, receive: Receive, tg: anyio.abc.TaskGroup) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                tg.cancel_scope.cancel()
                return


async def _read_body(receive: Receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            break
    return b"".join(chunks)


async def _plain(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _sse(send: Send, data: bytes) -> None:
    await send({"type": "http.response.body", "body": data, "more_body": True})


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #


def bind_socket(host: str = "127.0.0.1", port: int = 0) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


class ServerThread:
    """Run an ASGI app with uvicorn in a background thread (used by the fixture and tests)."""

    def __init__(
        self, app: Any, host: str = "127.0.0.1", port: int = 0, path: str = "/mcp"
    ) -> None:
        import uvicorn

        self.sock = bind_socket(host, port)
        self.host, self.port = self.sock.getsockname()[:2]
        self.path = path
        config = uvicorn.Config(
            app, log_level="warning", lifespan="on", access_log=False, timeout_graceful_shutdown=2
        )
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="polygraph-http", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def _run(self) -> None:
        self.server.run(sockets=[self.sock])

    def start(self, timeout: float = 10.0) -> ServerThread:
        self._thread.start()
        deadline = threading.Event()
        for _ in range(int(timeout / 0.02)):
            if self.server.started:
                return self
            deadline.wait(0.02)
        raise RuntimeError("HTTP server did not start in time")

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=5)
        with contextlib.suppress(OSError):
            self.sock.close()

    def __enter__(self) -> ServerThread:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def dumps_status(obj: Any) -> str:  # pragma: no cover - debugging helper
    return json.dumps(obj, indent=2)
