"""The proxy transports driven in-process: stdio (against a real world subprocess) and HTTP."""

from __future__ import annotations

import json
import sys

import anyio
import httpx
import pytest
from helpers import build_world, fault, http_proxy, http_world, world_cmd

from polygraph.faults import FaultPlan
from polygraph.jsonrpc import Message
from polygraph.ledger import read_ledger
from polygraph.proxy.http import HttpProxy, ServerThread
from polygraph.proxy.stdio import run_stdio_proxy
from polygraph.testing.client import McpClient

pytestmark = pytest.mark.integration

MODERN = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}


def call(i, tool, args, meta=True):
    params = {"name": tool, "arguments": args}
    if meta:
        params["_meta"] = MODERN
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": params}


async def drive(plan, ledger, cmd, lines, *, gap: float = 0.0, settle: float = 0.0):
    """Feed ``lines`` to the stdio proxy and collect what it writes back."""
    written: list[bytes] = []

    async def client_lines():
        for line in lines:
            if line == "WAIT":
                await anyio.sleep(gap)
                continue
            data = line if isinstance(line, bytes) else json.dumps(line).encode()
            yield data + b"\n"
        await anyio.sleep(settle)

    async def write(data: bytes) -> None:
        written.append(data)

    with anyio.fail_after(60):
        code = await run_stdio_proxy(
            plan, ledger, cmd, client_lines=client_lines(), client_write=write
        )
    out = []
    for chunk in written:
        for raw in chunk.splitlines():
            try:
                out.append(Message.parse(raw).raw)
            except ValueError:
                out.append({"raw": raw.decode("utf-8", "replace")})
    return code, out


@pytest.mark.anyio
async def test_stdio_passthrough_and_ledger(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    cmd = world_cmd("bank", "--db", str(tmp_path / "b.sqlite"), "--reset")
    code, out = await drive(
        FaultPlan(server="bank"),
        ledger,
        cmd,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": MODERN}},
            b"this is not json",
            call(2, "get_invoice", {"invoice_id": "INV-42"}),
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99}},
        ],
        settle=0.3,
    )
    assert code == 0
    by_id = {m.get("id"): m for m in out}
    assert [t["name"] for t in by_id[1]["result"]["tools"]][:1] == ["get_invoice"]
    assert by_id[2]["result"]["structuredContent"]["status"] == "open"
    assert by_id[None]["error"]["code"] == -32700  # the world answered the garbage line
    methods = [r.entry.method for r in read_ledger(ledger)]
    assert methods == ["tools/list", "tools/call"]


@pytest.mark.anyio
async def test_stdio_malformed_wire_and_lost_response(tmp_path):
    cmd = world_cmd("bank", "--db", str(tmp_path / "b.sqlite"), "--reset")
    plan = FaultPlan(
        server="bank",
        faults=[
            fault("malformed", "get_invoice", "bank", mode="wire"),
            fault("lost_response", "pay_invoice", "bank", **{"as": "rpc_error"}),
        ],
    )
    _, out = await drive(
        plan,
        tmp_path / "l.jsonl",
        cmd,
        [
            call(1, "get_invoice", {"invoice_id": "INV-42"}),
            call(2, "pay_invoice", {"invoice_id": "INV-42"}),
        ],
        settle=0.3,
    )
    raws = [m for m in out if "raw" in m]
    assert raws and raws[0]["raw"].startswith('{"jsonrpc"')
    lost = next(m for m in out if m.get("id") == 2)
    assert lost["error"]["code"] == -32000


@pytest.mark.anyio
async def test_stdio_timeout_cancelled_by_client(tmp_path):
    cmd = world_cmd("bank", "--db", str(tmp_path / "b.sqlite"), "--reset")
    plan = FaultPlan(server="bank", timeout_s=30, faults=[fault("timeout", "get_invoice", "bank")])
    with anyio.fail_after(20):
        _, out = await drive(
            plan,
            tmp_path / "l.jsonl",
            cmd,
            [
                call(1, "get_invoice", {"invoice_id": "INV-42"}),
                "WAIT",
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
            ],
            gap=0.3,
            settle=0.3,
        )
    assert not any(m.get("id") == 1 for m in out)  # cancelled: no reply at all
    rec = read_ledger(tmp_path / "l.jsonl")[0]
    assert rec.entry.told is None and "cancelled" in (rec.entry.happened.note or "")


@pytest.mark.anyio
async def test_stdio_server_that_exits_ends_the_proxy(tmp_path):
    cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
    code, out = await drive(
        FaultPlan(server="x"), tmp_path / "l.jsonl", cmd, ["WAIT", call(1, "t", {})], gap=0.5
    )
    assert code == 3 and out == []


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def post(url, body, headers=None, **kw):
    h = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    h.update(headers or {})
    return httpx.post(url, content=json.dumps(body), headers=h, timeout=20, **kw)


def test_http_passthrough_errors_paths_and_sessions(tmp_path):
    with (
        http_world("bank", tmp_path / "b.sqlite") as (up, _db),
        http_proxy(FaultPlan(server="bank"), tmp_path / "l.jsonl", up) as url,
    ):
        # modern request with a header mismatch: the upstream 400 is passed through
        r = post(
            url,
            call(1, "get_invoice", {"invoice_id": "INV-42"}),
            {"mcp-protocol-version": "2025-06-18", "mcp-method": "tools/call"},
        )
        assert r.status_code == 400 and r.json()["error"]["code"] == -32020
        # unknown path
        assert httpx.get(url.replace("/mcp", "/nope"), timeout=10).status_code == 404
        # legacy session: initialize mints a session id that flows back through the proxy
        init = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
        r = post(url, init)
        sid = r.headers["mcp-session-id"]
        assert r.json()["result"]["protocolVersion"] == "2025-06-18"
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert post(url, note, {"mcp-session-id": sid}).status_code == 202
        r = post(
            url,
            call(3, "get_invoice", {"invoice_id": "INV-42"}, meta=False),
            {"mcp-session-id": sid},
        )
        assert r.json()["result"]["structuredContent"]["invoice_id"] == "INV-42"
        assert httpx.delete(url, headers={"mcp-session-id": sid}, timeout=10).status_code == 200
        # a body that is not JSON-RPC is passed through untouched
        r = httpx.post(
            url, content=b"[1, 2]", headers={"content-type": "application/json"}, timeout=10
        )
        assert r.status_code == 400
    recs = read_ledger(tmp_path / "l.jsonl")
    calls = [r for r in recs if r.entry.method == "tools/call"]
    # the body's _meta version wins over the header; the legacy call uses the session's version
    assert [c.entry.protocol_version for c in calls] == ["2026-07-28", "2025-06-18"]


def test_http_upstream_unreachable(tmp_path):
    with http_proxy(
        FaultPlan(server="bank"), tmp_path / "l.jsonl", "http://127.0.0.1:9/mcp"
    ) as url:
        r = post(
            url,
            call(1, "get_invoice", {"invoice_id": "INV-42"}),
            {"mcp-protocol-version": "2026-07-28"},
        )
        assert r.status_code in (502, 504)
        r = httpx.delete(url, timeout=10)
        assert r.status_code == 502


def test_http_legacy_rug_pull_inline_and_get_stream(tmp_path):
    plan = FaultPlan(
        server="inbox",
        faults=[fault("description_poison", "send_email", "inbox", mode="rug_pull")],
    )
    with (
        http_world("inbox", tmp_path / "i.sqlite") as (up, _db),
        http_proxy(plan, tmp_path / "l.jsonl", up) as url,
        McpClient.connect({"url": url}, era="legacy") as client,
    ):
        assert client.protocol_version == "2025-11-25"
        client.list_tools()
        client.call_tool("read_email", {"email_id": "EM-1"})
        # the list_changed came inline on that response's SSE stream
        assert any(m.method == "notifications/tools/list_changed" for m in client.notifications)
        assert client.tools_stale
        tools = {t["name"]: t for t in client.list_tools()}
        assert "<IMPORTANT>" in tools["send_email"]["description"]


def test_http_proxy_serves_get_stream_for_rug_pull(tmp_path):
    plan = FaultPlan(
        server="inbox",
        faults=[fault("description_poison", "send_email", "inbox", mode="rug_pull")],
    )
    with (
        http_world("inbox", tmp_path / "i.sqlite") as (up, _db),
        http_proxy(plan, tmp_path / "l.jsonl", up) as url,
    ):
        client = McpClient.connect({"url": url}, era="legacy")
        try:
            client.listen()  # legacy HTTP: opens the GET stream
            anyio.run(anyio.sleep, 0.3)
            client.transport.base_headers["accept"] = "application/json"  # no inline SSE
            client.list_tools()
            client.call_tool("read_email", {"email_id": "EM-1"})
            for _ in range(50):
                if any(
                    m.method == "notifications/tools/list_changed" for m in client.notifications
                ):
                    break
                anyio.run(anyio.sleep, 0.05)
            assert any(m.method == "notifications/tools/list_changed" for m in client.notifications)
        finally:
            client.close()


def test_http_sse_upstream_with_modern_listen(tmp_path):
    plan = FaultPlan(
        server="inbox",
        faults=[fault("description_poison", "send_email", "inbox", mode="rug_pull", ttl_ms=60000)],
    )
    with (
        http_world("inbox", tmp_path / "i.sqlite", sse=True) as (up, _db),
        http_proxy(plan, tmp_path / "l.jsonl", up) as url,
        McpClient.connect({"url": url}, era="modern") as client,
    ):
        client.listen()
        anyio.run(anyio.sleep, 0.3)
        client.list_tools()
        assert not client.tools_stale
        client.call_tool("read_email", {"email_id": "EM-1"})
        for _ in range(50):
            if any(m.method == "notifications/tools/list_changed" for m in client.notifications):
                break
            anyio.run(anyio.sleep, 0.05)
        acks = [
            m
            for m in client.notifications
            if m.method == "notifications/subscriptions/acknowledged"
        ]
        changed = [
            m for m in client.notifications if m.method == "notifications/tools/list_changed"
        ]
        assert acks and changed
        assert changed[0].params["_meta"]["io.modelcontextprotocol/subscriptionId"]


def test_server_thread_context_and_proxy_lifespan(tmp_path):
    server, _ = build_world("bank", tmp_path / "b.sqlite")
    proxy = HttpProxy(FaultPlan(server="bank"), None, "http://127.0.0.1:9/mcp")
    with ServerThread(server.asgi_app()) as t, ServerThread(proxy) as p:
        assert t.url.endswith("/mcp") and p.port != t.port
