"""JSON-RPC 2.0 messages and the two MCP framings (newline-delimited JSON and SSE).

The proxy works at the message level, so this module is deliberately small
and tolerant: anything it does not understand is kept as raw bytes and passed
through untouched.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Implementation-defined server errors (-32000 to -32019) used by Polygraph faults.
CONNECTION_LOST = -32000  # matches what common SDKs use for "Connection closed"
REQUEST_TIMEOUT = -32001  # matches what common SDKs use for "Request timed out"


class ParseError(ValueError):
    """Raised when bytes are not a JSON-RPC message."""


class Kind(StrEnum):
    REQUEST = "request"
    NOTIFICATION = "notification"
    RESPONSE = "response"  # result or error


@dataclass
class Message:
    """One JSON-RPC message. ``raw`` is the decoded JSON object, kept as-is."""

    raw: dict[str, Any]
    kind: Kind = field(init=False)

    def __post_init__(self) -> None:
        if "method" in self.raw:
            self.kind = Kind.REQUEST if "id" in self.raw else Kind.NOTIFICATION
        elif "result" in self.raw or "error" in self.raw:
            self.kind = Kind.RESPONSE
        else:
            raise ParseError("not a JSON-RPC message (no method, result or error)")

    # -- accessors ---------------------------------------------------------
    @property
    def id(self) -> Any:
        return self.raw.get("id")

    @property
    def method(self) -> str | None:
        m = self.raw.get("method")
        return m if isinstance(m, str) else None

    @property
    def params(self) -> dict[str, Any]:
        p = self.raw.get("params")
        return p if isinstance(p, dict) else {}

    @property
    def result(self) -> Any:
        return self.raw.get("result")

    @property
    def error(self) -> dict[str, Any] | None:
        e = self.raw.get("error")
        return e if isinstance(e, dict) else None

    @property
    def is_request(self) -> bool:
        return self.kind is Kind.REQUEST

    @property
    def is_notification(self) -> bool:
        return self.kind is Kind.NOTIFICATION

    @property
    def is_response(self) -> bool:
        return self.kind is Kind.RESPONSE

    # -- (de)serialisation -------------------------------------------------
    @classmethod
    def parse(cls, data: str | bytes | dict[str, Any]) -> Message:
        if isinstance(data, dict):
            return cls(data)
        try:
            obj = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError(str(exc)) from exc
        if not isinstance(obj, dict):
            raise ParseError("JSON-RPC message must be an object")
        return cls(obj)

    def to_json(self) -> str:
        return json.dumps(self.raw, ensure_ascii=False, separators=(",", ":"))

    def to_line(self) -> bytes:
        """Newline-delimited JSON framing for stdio."""
        return self.to_json().encode("utf-8") + b"\n"

    def copy(self) -> Message:
        return Message(json.loads(json.dumps(self.raw)))


def request(id: Any, method: str, params: dict[str, Any] | None = None) -> Message:
    raw: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method}
    if params is not None:
        raw["params"] = params
    return Message(raw)


def notification(method: str, params: dict[str, Any] | None = None) -> Message:
    raw: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        raw["params"] = params
    return Message(raw)


def response(id: Any, result: Any) -> Message:
    return Message({"jsonrpc": JSONRPC_VERSION, "id": id, "result": result})


def error_response(id: Any, code: int, message: str, data: Any = None) -> Message:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return Message({"jsonrpc": JSONRPC_VERSION, "id": id, "error": err})


def id_key(id: Any) -> str:
    """A hashable key that keeps ``1`` and ``"1"`` apart."""
    return json.dumps(id, sort_keys=True)


# --------------------------------------------------------------------------- #
# Server-Sent Events (Streamable HTTP)
# --------------------------------------------------------------------------- #


def encode_sse(data: str, event: str | None = "message") -> bytes:
    lines = []
    if event:
        lines.append(f"event: {event}")
    for chunk in data.split("\n"):
        lines.append(f"data: {chunk}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def encode_sse_message(msg: Message) -> bytes:
    return encode_sse(msg.to_json())


SSE_KEEPALIVE = b":\n\n"


@dataclass
class SseEvent:
    event: str
    data: str


class SseParser:
    """Incremental SSE parser. Feed bytes, get complete events back."""

    def __init__(self) -> None:
        self._buf = b""
        self._event = ""
        self._data: list[str] = []
        #: the last line ended with a "\r" at the very end of a chunk; a "\n" that
        #: starts the next chunk belongs to that same line ending ("\r\n" split in two)
        self._skip_lf = False

    def feed(self, chunk: bytes) -> Iterator[SseEvent]:
        if self._skip_lf and chunk:
            self._skip_lf = False
            if chunk.startswith(b"\n"):
                chunk = chunk[1:]
        self._buf += chunk
        while True:
            # Accept \n, \r\n and \r line endings.
            idx = -1
            for sep in (b"\n", b"\r"):
                i = self._buf.find(sep)
                if i != -1 and (idx == -1 or i < idx):
                    idx = i
            if idx == -1:
                return
            line = self._buf[:idx]
            rest = self._buf[idx + 1 :]
            if self._buf[idx : idx + 1] == b"\r":
                if rest.startswith(b"\n"):
                    rest = rest[1:]
                elif not rest:
                    self._skip_lf = True
            self._buf = rest
            ev = self._line(line.decode("utf-8", errors="replace"))
            if ev is not None:
                yield ev

    def flush(self) -> Iterator[SseEvent]:
        if self._buf:
            ev = self._line(self._buf.decode("utf-8", errors="replace"))
            self._buf = b""
            if ev is not None:
                yield ev
        ev = self._line("")
        if ev is not None:
            yield ev

    def _line(self, line: str) -> SseEvent | None:
        if line == "":
            if not self._data:
                self._event = ""
                return None
            ev = SseEvent(self._event or "message", "\n".join(self._data))
            self._event, self._data = "", []
            return ev
        if line.startswith(":"):
            return None  # comment / keep-alive
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        return None
