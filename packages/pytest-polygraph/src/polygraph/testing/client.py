"""A small synchronous MCP client for tests, demos and the planner.

Speaks both protocol eras over both transports:

- ``era="legacy"``: ``initialize`` handshake (version ``2025-11-25`` by default),
  ``Mcp-Session-Id`` on HTTP, ``notifications/tools/list_changed``;
- ``era="modern"``: 2026-07-28, ``_meta`` on every request, the
  ``MCP-Protocol-Version``/``Mcp-Method``/``Mcp-Name`` headers on HTTP,
  ``server/discover``, ``subscriptions/listen``, ``ttlMs`` tool-list caching;
- ``era="auto"`` (default): probe with ``server/discover`` and fall back to
  ``initialize`` when the server does not speak the modern era.

It is synchronous on purpose (background threads read the wire), so scripted
agents and plain pytest tests can use it without an event loop.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..jsonrpc import (
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    Message,
    ParseError,
    SseParser,
    error_response,
    id_key,
    notification,
    request,
)
from ..protocol import (
    CANCELLED,
    HEADER_METHOD,
    HEADER_NAME,
    HEADER_PROTOCOL_VERSION,
    HEADER_SESSION_ID,
    LATEST_LEGACY_VERSION,
    LATEST_MODERN_VERSION,
    MODERN_ERROR_CODES,
    TOOLS_LIST_CHANGED,
    header_value,
    is_modern,
    modern_meta,
    tool_text,
)


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message
        self.data = data


class McpTimeout(McpError):
    def __init__(self, seconds: float) -> None:
        super().__init__(REQUEST_TIMEOUT, f"no response within {seconds:g} s")


class McpConnectionError(McpError):
    def __init__(self, message: str) -> None:
        super().__init__(-32000, message)


@dataclass
class ToolResult:
    """What an agent sees from one tool call."""

    text: str = ""
    structured: Any = None
    is_error: bool = False
    raw: dict[str, Any] | None = None
    error: dict[str, Any] | None = None  # JSON-RPC error, if the call failed at protocol level

    @property
    def ok(self) -> bool:
        return not self.is_error

    def data(self) -> Any:
        """Structured content, else the text parsed as JSON, else None."""
        if self.structured is not None:
            return self.structured
        try:
            return json.loads(self.text)
        except ValueError:
            return None


Handler = Callable[[Message], None]


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


class _Pending:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: dict[str, tuple[threading.Event, list[Message]]] = {}

    def add(self, mid: Any) -> tuple[threading.Event, list[Message]]:
        ev, slot = threading.Event(), list[Message]()
        with self.lock:
            self.items[id_key(mid)] = (ev, slot)
        return ev, slot

    def resolve(self, msg: Message) -> bool:
        with self.lock:
            item = self.items.pop(id_key(msg.id), None)
        if item is None:
            return False
        item[1].append(msg)
        item[0].set()
        return True

    def drop(self, mid: Any) -> None:
        with self.lock:
            self.items.pop(id_key(mid), None)

    def fail_all(self) -> None:
        with self.lock:
            items, self.items = self.items, {}
        for ev, _ in items.values():
            ev.set()


class StdioTransport:
    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        self.proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=full_env,
            cwd=cwd,
            bufsize=0,
        )
        self._pending = _Pending()
        self._write_lock = threading.Lock()
        self.on_message: Handler = lambda m: None
        self.closed = False
        self._reader = threading.Thread(target=self._read, name="mcp-stdio-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = Message.parse(line)
            except ParseError:
                continue  # garbage on the wire: the request waits for its timeout
            if msg.is_response and self._pending.resolve(msg):
                continue
            if msg.is_request:  # legacy server-to-client request: we support none
                self.send(error_response(msg.id, METHOD_NOT_FOUND, "not supported by client"))
                continue
            self.on_message(msg)
        self.closed = True
        self._pending.fail_all()

    def send(self, msg: Message) -> None:
        if self.closed or self.proc.stdin is None:
            raise McpConnectionError("server closed the connection")
        with self._write_lock:
            try:
                self.proc.stdin.write(msg.to_line())
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self.closed = True
                raise McpConnectionError("server closed the connection") from exc

    def request(self, msg: Message, timeout: float, headers: dict[str, str]) -> Message:
        ev, slot = self._pending.add(msg.id)
        self.send(msg)
        if not ev.wait(timeout):
            self._pending.drop(msg.id)
            with contextlib.suppress(McpError):
                self.send(notification(CANCELLED, {"requestId": msg.id, "reason": "timeout"}))
            raise McpTimeout(timeout)
        if not slot:
            raise McpConnectionError("server closed the connection")
        return slot[0]

    def notify(self, msg: Message, headers: dict[str, str]) -> None:
        self.send(msg)

    def stream(self, msg: Message, headers: dict[str, str]) -> None:
        """stdio: a long-lived request is just a request whose answer comes later."""
        self._pending.add(msg.id)
        self.send(msg)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    self.proc.kill()
        if self.proc.stdout:
            self.proc.stdout.close()
        self.closed = True


class HttpTransport:
    def __init__(self, url: str, *, headers: Mapping[str, str] | None = None) -> None:
        self.url = url
        self.client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=None)
        )
        self.base_headers = {"accept": "application/json, text/event-stream", **(headers or {})}
        self.session_id: str | None = None
        self.on_message: Handler = lambda m: None
        self.closed = False
        self._streams: list[threading.Thread] = []
        self._stop = threading.Event()

    def _headers(self, extra: dict[str, str]) -> dict[str, str]:
        h = dict(self.base_headers)
        h["content-type"] = "application/json"
        if self.session_id:
            h[HEADER_SESSION_ID] = self.session_id
        h.update(extra)
        return h

    def request(self, msg: Message, timeout: float, headers: dict[str, str]) -> Message:
        result: list[Message | BaseException] = []
        done = threading.Event()

        def work() -> None:
            try:
                result.append(self._post(msg, headers))
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                result.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=work, daemon=True, name="mcp-http-request")
        t.start()
        if not done.wait(timeout):
            raise McpTimeout(timeout)
        out = result[0]
        if isinstance(out, BaseException):
            raise out
        return out

    def _post(self, msg: Message, headers: dict[str, str]) -> Message:
        try:
            with self.client.stream(
                "POST", self.url, content=msg.to_json().encode(), headers=self._headers(headers)
            ) as r:
                sid = r.headers.get(HEADER_SESSION_ID)
                if sid:
                    self.session_id = sid
                ctype = r.headers.get("content-type", "")
                if "text/event-stream" in ctype and r.status_code == 200:
                    parser = SseParser()
                    for chunk in r.iter_bytes():
                        for ev in parser.feed(chunk):
                            try:
                                m = Message.parse(ev.data)
                            except ParseError:
                                continue
                            if m.is_response and id_key(m.id) == id_key(msg.id):
                                return m
                            self.on_message(m)
                    raise McpConnectionError("stream ended without a response")
                body = r.read()
                try:
                    reply = Message.parse(body)
                except ParseError as exc:
                    raise McpError(-32000, f"HTTP {r.status_code}: not a JSON-RPC reply") from exc
                if r.status_code >= 400 and reply.error is None:
                    raise McpError(-32000, f"HTTP {r.status_code}")
                return reply
        except httpx.HTTPError as exc:
            raise McpConnectionError(f"HTTP error: {type(exc).__name__}") from exc

    def notify(self, msg: Message, headers: dict[str, str]) -> None:
        try:
            self.client.post(
                self.url, content=msg.to_json().encode(), headers=self._headers(headers)
            )
        except httpx.HTTPError as exc:
            raise McpConnectionError(f"HTTP error: {type(exc).__name__}") from exc

    def stream(self, msg: Message, headers: dict[str, str]) -> None:
        """Open a long-lived POST (``subscriptions/listen``) and dispatch its notifications."""
        hdrs = self._headers(headers)

        def work() -> None:
            try:
                with self.client.stream(
                    "POST", self.url, content=msg.to_json().encode(), headers=hdrs
                ) as r:
                    parser = SseParser()
                    for chunk in r.iter_bytes():
                        if self._stop.is_set():
                            return
                        for ev in parser.feed(chunk):
                            try:
                                self.on_message(Message.parse(ev.data))
                            except ParseError:
                                continue
            except (httpx.HTTPError, RuntimeError):
                return

        t = threading.Thread(target=work, daemon=True, name="mcp-http-stream")
        t.start()
        self._streams.append(t)

    def open_get_stream(self) -> None:
        """Legacy standalone SSE stream for server-initiated notifications."""
        hdrs = self._headers({})

        def work() -> None:
            try:
                with self.client.stream("GET", self.url, headers=hdrs) as r:
                    if r.status_code != 200:
                        return
                    parser = SseParser()
                    for chunk in r.iter_bytes():
                        if self._stop.is_set():
                            return
                        for ev in parser.feed(chunk):
                            try:
                                self.on_message(Message.parse(ev.data))
                            except ParseError:
                                continue
            except (httpx.HTTPError, RuntimeError):
                return

        t = threading.Thread(target=work, daemon=True, name="mcp-http-get")
        t.start()
        self._streams.append(t)

    def close(self) -> None:
        self._stop.set()
        if self.session_id:
            with contextlib.suppress(httpx.HTTPError):
                self.client.delete(self.url, headers=self._headers({}), timeout=5)
        self.client.close()
        self.closed = True


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #


@dataclass
class _ToolCache:
    tools: list[dict[str, Any]] = field(default_factory=list)
    fetched_at: float = 0.0
    ttl_ms: int | None = None
    dirty: bool = True


class McpClient:
    def __init__(
        self,
        transport: StdioTransport | HttpTransport,
        *,
        era: str = "auto",
        legacy_version: str = LATEST_LEGACY_VERSION,
        modern_version: str = LATEST_MODERN_VERSION,
        timeout: float = 30.0,
        client_name: str = "polygraph-testing",
    ) -> None:
        self.transport = transport
        self.era = era
        self.legacy_version = legacy_version
        self.modern_version = modern_version
        self.timeout = timeout
        self.client_name = client_name
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.notifications: list[Message] = []
        self._ids = itertools.count(1)
        self._cache = _ToolCache()
        transport.on_message = self._on_message
        self._open()

    # ----------------------------------------------------------- construction
    @classmethod
    def connect(cls, config: Mapping[str, Any], **kw: Any) -> McpClient:
        """Connect using a server config.

        ``{"command": [...], "env": {...}}`` for stdio or ``{"url": ...}`` for HTTP.
        """
        if "command" in config:
            transport: StdioTransport | HttpTransport = StdioTransport(
                config["command"], env=config.get("env"), cwd=config.get("cwd")
            )
        elif "url" in config:
            transport = HttpTransport(config["url"], headers=config.get("headers"))
        else:
            raise ValueError("server config needs 'command' or 'url'")
        try:
            return cls(transport, **kw)
        except BaseException:
            transport.close()
            raise

    def __enter__(self) -> McpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.transport.close()

    @property
    def modern(self) -> bool:
        return is_modern(self.protocol_version)

    def _open(self) -> None:
        if self.era == "legacy":
            self._initialize()
            return
        if self.era == "modern":
            self.protocol_version = self.modern_version
            return
        # auto: probe
        try:
            self.protocol_version = self.modern_version
            res = self.request("server/discover", {})
            versions = res.get("supportedVersions") or []
            modern = [v for v in versions if is_modern(v)]
            if modern:
                if self.modern_version not in modern:
                    self.protocol_version = modern[0]
                info = (res.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo")
                self.server_info = info or {}
                return
        except McpError as exc:
            if exc.code in MODERN_ERROR_CODES and isinstance(exc.data, dict):
                supported = [v for v in exc.data.get("supported", []) if is_modern(v)]
                if supported:
                    self.protocol_version = supported[0]
                    return
        self.protocol_version = None
        self._initialize()

    def _initialize(self) -> None:
        res = self.request(
            "initialize",
            {
                "protocolVersion": self.legacy_version,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "1"},
            },
        )
        self.protocol_version = str(res.get("protocolVersion") or self.legacy_version)
        self.server_info = res.get("serverInfo") or {}
        self.transport.notify(notification("notifications/initialized"), self._headers("x", None))

    # ----------------------------------------------------------- messaging
    def _on_message(self, msg: Message) -> None:
        self.notifications.append(msg)
        if msg.method == TOOLS_LIST_CHANGED:
            self._cache.dirty = True

    def _headers(self, method: str, name: str | None) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.protocol_version and (self.modern or self.protocol_version >= "2025-06-18"):
            h[HEADER_PROTOCOL_VERSION] = self.protocol_version
        if self.modern:
            h[HEADER_METHOD] = method
            if name is not None:
                h[HEADER_NAME] = header_value(name)
        return h

    def _params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        p = dict(params or {})
        if self.modern:
            meta = dict(p.get("_meta") or {})
            meta.update(
                modern_meta(
                    self.protocol_version or self.modern_version, client_name=self.client_name
                )
            )
            p["_meta"] = meta
        return p

    def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        msg = request(next(self._ids), method, self._params(params))
        name = (params or {}).get("name") if method == "tools/call" else None
        reply = self.transport.request(msg, timeout or self.timeout, self._headers(method, name))
        if reply.error is not None:
            err = reply.error
            raise McpError(
                int(err.get("code", -32000)), str(err.get("message", "")), err.get("data")
            )
        result = reply.result
        return result if isinstance(result, dict) else {}

    # ----------------------------------------------------------- tools
    @property
    def tools_stale(self) -> bool:
        c = self._cache
        if c.dirty:
            return True
        if c.ttl_ms is not None:
            return (time.perf_counter() - c.fetched_at) * 1000 >= c.ttl_ms
        return False

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and not self.tools_stale:
            return list(self._cache.tools)
        tools: list[dict[str, Any]] = []
        cursor = None
        ttl: int | None = None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in res.get("tools") or [] if isinstance(t, dict))
            if isinstance(res.get("ttlMs"), int):
                ttl = res["ttlMs"] if ttl is None else min(ttl, res["ttlMs"])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        self._cache = _ToolCache(
            tools=tools, fetched_at=time.perf_counter(), ttl_ms=ttl, dirty=False
        )
        return list(tools)

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> ToolResult:
        """Call a tool.

        Protocol errors and timeouts come back as an error result, not an exception.
        """
        try:
            res = self.request(
                "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
            )
        except McpError as exc:
            return ToolResult(
                text=exc.message, is_error=True, error={"code": exc.code, "message": exc.message}
            )
        return ToolResult(
            text=tool_text(res),
            structured=res.get("structuredContent"),
            is_error=bool(res.get("isError")),
            raw=res,
        )

    def listen(self, *, tools: bool = True) -> None:
        """Open a ``subscriptions/listen`` stream (modern) or the GET stream (legacy HTTP)."""
        if not self.modern:
            if isinstance(self.transport, HttpTransport):
                self.transport.open_get_stream()
            return
        msg = request(
            f"listen-{next(self._ids)}",
            "subscriptions/listen",
            self._params({"notifications": {"toolsListChanged": tools}}),
        )
        self.transport.stream(msg, self._headers("subscriptions/listen", None))
