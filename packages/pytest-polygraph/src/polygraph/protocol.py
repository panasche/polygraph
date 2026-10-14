"""MCP protocol constants and helpers shared by the proxy, the worlds and the client.

Two eras are supported:

- *legacy* (2025-11-25 and earlier): an ``initialize`` handshake fixes the
  protocol version for the session; ``Mcp-Session-Id`` on HTTP;
  ``notifications/tools/list_changed`` on the session.
- *modern* (2026-07-28): no handshake; every request carries its protocol
  version in ``_meta``; ``server/discover``; change notifications on a
  ``subscriptions/listen`` stream; ``ttlMs``/``cacheScope`` on list results;
  every result carries ``resultType``.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from .jsonrpc import Message

LATEST_MODERN_VERSION = "2026-07-28"
LATEST_LEGACY_VERSION = "2025-11-25"
LEGACY_VERSIONS: tuple[str, ...] = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MODERN_VERSIONS: tuple[str, ...] = ("2026-07-28",)
KNOWN_VERSIONS: tuple[str, ...] = LEGACY_VERSIONS + MODERN_VERSIONS

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
META_SUBSCRIPTION_ID = "io.modelcontextprotocol/subscriptionId"
META_TRACEPARENT = "traceparent"
META_TRACESTATE = "tracestate"

HEADER_PROTOCOL_VERSION = "mcp-protocol-version"
HEADER_SESSION_ID = "mcp-session-id"
HEADER_METHOD = "mcp-method"
HEADER_NAME = "mcp-name"

TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
SUBSCRIPTION_ACK = "notifications/subscriptions/acknowledged"
CANCELLED = "notifications/cancelled"

# Error codes reserved by the 2026-07-28 spec.
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022
MODERN_ERROR_CODES = frozenset(
    {HEADER_MISMATCH, MISSING_REQUIRED_CLIENT_CAPABILITY, UNSUPPORTED_PROTOCOL_VERSION}
)


def is_modern(version: str | None) -> bool:
    """True for per-request-metadata versions. Unknown future versions count as modern."""
    if not version:
        return False
    if version in LEGACY_VERSIONS:
        return False
    return version in MODERN_VERSIONS or version > LATEST_LEGACY_VERSION


def meta_of(params: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else {}


def request_version(msg: Message) -> str | None:
    """Protocol version declared in a request's ``_meta`` (modern clients only)."""
    v = meta_of(msg.params).get(META_PROTOCOL_VERSION)
    return v if isinstance(v, str) else None


def modern_meta(
    version: str = LATEST_MODERN_VERSION,
    *,
    client_name: str = "polygraph",
    client_version: str = "0",
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        META_PROTOCOL_VERSION: version,
        META_CLIENT_INFO: {"name": client_name, "version": client_version},
        META_CLIENT_CAPABILITIES: capabilities or {},
    }


def strip_meta(params: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    return {k: v for k, v in params.items() if k != "_meta"}


def tool_text(result: Any) -> str:
    """All text content of a tool result joined with newlines."""
    if not isinstance(result, dict):
        return ""
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def tool_result(
    text: str,
    *,
    is_error: bool = False,
    structured: Any = None,
    modern: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        out["structuredContent"] = structured
    if modern:
        out["resultType"] = "complete"
    return out


def is_error_response(msg: Message | None) -> bool:
    """True when a response is a JSON-RPC error or a tool result with ``isError``."""
    if msg is None:
        return False
    if msg.error is not None:
        return True
    res = msg.result
    return isinstance(res, dict) and bool(res.get("isError"))


def header_value(value: str) -> str:
    """Encode a value for an ``Mcp-Name``/``Mcp-Param-*`` header (base64 sentinel if needed)."""
    safe = (
        value == value.strip()
        and all(0x20 <= ord(c) <= 0x7E for c in value)
        and not (value.startswith("=?base64?") and value.endswith("?="))
    )
    if safe:
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def decode_header_value(value: str) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        return base64.b64decode(value[9:-2]).decode("utf-8")
    return value


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
