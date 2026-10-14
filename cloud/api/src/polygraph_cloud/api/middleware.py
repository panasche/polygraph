"""One pure-ASGI middleware for cross-cutting concerns.

- request id (``X-Request-ID``; accepted from the caller only if it looks safe)
- client IP from ``X-Forwarded-For`` with a fixed number of trusted proxy hops
- body size limit for everything except the upload endpoint (which streams
  with its own, larger cap)
- CSRF double-submit check on every mutating request that carries a session
- applying cookies queued by handlers (so 204 responses can set cookies too)
- security headers and ``Cache-Control: no-store`` unless a route set its own
- one access-log line per request: method, path (never the query string), status, time
"""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import Settings
from ..errors import envelope
from ..logs import get_logger
from ..security import constant_time_equals, csrf_for_session

log = get_logger("polygraph_cloud.access")

SESSION_COOKIE = "pg_session"
CSRF_COOKIE = "pg_csrf"
CSRF_HEADER = b"x-csrf-token"
STATE_COOKIE = "pg_oauth_state"

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_REQ_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_CSRF_EXEMPT = ("/api/upload", "/api/auth/dev-login")
_STREAMED = ("/api/upload",)


def parse_cookies(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and name not in out:
            out[name] = value.strip().strip('"')
    return out


def header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


def client_ip(scope: Scope, hops: int) -> str:
    peer = (scope.get("client") or ("unknown", 0))[0] or "unknown"
    if hops <= 0:
        return peer
    xff = header(scope, b"x-forwarded-for")
    if not xff:
        return peer
    chain = [p.strip() for p in xff.split(",") if p.strip()]
    if not chain:
        return peer
    return chain[-hops] if len(chain) >= hops else chain[0]


def cookie_header(
    name: str,
    value: str,
    *,
    max_age: int | None,
    secure: bool,
    http_only: bool = True,
    path: str = "/",
    same_site: str = "Lax",
) -> str:
    parts = [f"{name}={value}", f"Path={path}", f"SameSite={same_site}"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
        if max_age == 0:
            parts.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def queue_cookie(scope_state: dict[str, Any], value: str) -> None:
    scope_state.setdefault("pg_set_cookies", []).append(value)


class ApiMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.secret = settings.secret("session_secret")
        self.docs_paths = ("/api/docs", "/api/openapi.json") if not settings.is_production else ()

    async def _reply(
        self, send: Send, status: int, code: str, message: str, request_id: str
    ) -> None:
        body = json.dumps(envelope(code, message)).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"x-request-id", request_id.encode()),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _csrf_ok(self, scope: Scope, cookies: dict[str, str]) -> bool:
        session = cookies.get(SESSION_COOKIE)
        if not session:
            return True  # nothing to ride on
        sent = header(scope, CSRF_HEADER)
        if not sent:
            return False
        if not constant_time_equals(sent, cookies.get(CSRF_COOKIE)):
            return False
        if not constant_time_equals(sent, csrf_for_session(self.secret, session)):
            return False
        origin = header(scope, b"origin")
        return not (origin and origin not in self.settings.allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        incoming = header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQ_ID.match(incoming) else secrets.token_hex(12)
        # Per-request copy: some servers share the lifespan state dict between requests.
        state = dict(scope.get("state") or {})
        scope["state"] = state
        state["request_id"] = request_id
        state["client_ip"] = client_ip(scope, self.settings.trusted_proxy_hops)
        method = scope["method"]
        path: str = scope["path"]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_holder = {"status": 500}

        async def finish() -> None:
            log.info(
                "request",
                method=method,
                path=path,
                status=status_holder["status"],
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )

        # ---- body limit ---------------------------------------------------------
        if method not in _SAFE_METHODS and not path.startswith(_STREAMED):
            limit = self.settings.max_request_bytes
            length = header(scope, b"content-length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                status_holder["status"] = 413
                await self._reply(
                    send, 413, "payload_too_large", "Request body too large.", request_id
                )
                await finish()
                return
            chunks: list[bytes] = []
            total = 0
            more = True
            while more:
                msg = await receive()
                if msg["type"] == "http.disconnect":
                    return
                chunk = msg.get("body", b"")
                total += len(chunk)
                if total > limit:
                    status_holder["status"] = 413
                    await self._reply(
                        send, 413, "payload_too_large", "Request body too large.", request_id
                    )
                    await finish()
                    return
                chunks.append(chunk)
                more = msg.get("more_body", False)
            body = b"".join(chunks)
            replayed = False

            async def replay() -> Message:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            receive = replay

            # ---- CSRF ------------------------------------------------------------
            if not path.startswith(_CSRF_EXEMPT):
                cookies = parse_cookies(header(scope, b"cookie") or "")
                if not self._csrf_ok(scope, cookies):
                    status_holder["status"] = 403
                    await self._reply(
                        send, 403, "forbidden", "Missing or invalid CSRF token.", request_id
                    )
                    await finish()
                    return

        hsts = self.settings.cookie_secure
        is_docs = path.startswith(self.docs_paths) if self.docs_paths else False

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}

                def add(name: bytes, value: str) -> None:
                    if name not in present:
                        headers.append((name, value.encode("latin-1")))
                        present.add(name)

                for c in state.get("pg_set_cookies", []):
                    headers.append((b"set-cookie", c.encode("latin-1")))
                add(b"x-request-id", request_id)
                add(b"x-content-type-options", "nosniff")
                add(b"x-frame-options", "DENY")
                add(b"referrer-policy", "no-referrer")
                add(b"cross-origin-opener-policy", "same-origin")
                add(b"cross-origin-resource-policy", "same-site")
                add(b"permissions-policy", "camera=(), microphone=(), geolocation=()")
                if not is_docs:
                    add(b"content-security-policy", "default-src 'none'; frame-ancestors 'none'")
                if hsts:
                    add(b"strict-transport-security", "max-age=63072000; includeSubDomains")
                add(b"cache-control", "no-store")
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            await finish()
            structlog.contextvars.clear_contextvars()
