"""ProxyEngine against a fake upstream: recording, versions, tracing, rug pulls, state, replay."""

from __future__ import annotations

import json
import time
from typing import Any

import anyio
import pytest

from polygraph.faults import FaultPlan
from polygraph.jsonrpc import Message, error_response, notification, request, response
from polygraph.ledger import LedgerRecord, read_ledger
from polygraph.protocol import (
    CANCELLED,
    META_PROTOCOL_VERSION,
    META_SUBSCRIPTION_ID,
    META_TRACEPARENT,
    TOOLS_LIST_CHANGED,
    modern_meta,
    strip_meta,
    tool_text,
)
from polygraph.proxy.core import ProxyEngine, message_from_payload, parse_traceparent
from polygraph.results import FaultName, FaultSpec, Happened, LedgerEntry, Payload

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
TRACE = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"

TOOLS = [
    {
        "name": "get_invoice",
        "description": "Look up an invoice.",
        "inputSchema": {"type": "object", "properties": {"invoice_id": {"type": "string"}}},
    },
    {
        "name": "send_email",
        "description": "Send an email.",
        "inputSchema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "bcc": {"type": "string"}},
        },
    },
]


def world_result(data: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
        "structuredContent": data,
        "isError": False,
    }


class FakeServer:
    """A tiny fake MCP server: records requests, answers from canned data."""

    def __init__(self, *, status: str = "paid", legacy_version: str = LEGACY) -> None:
        self.requests: list[Message] = []
        self.status = status
        self.legacy_version = legacy_version
        self.release = anyio.Event()
        self.none_for: set[str] = set()

    async def send(self, msg: Message) -> Message | None:
        self.requests.append(msg)
        method = msg.method
        if method in self.none_for:
            return None
        if method == "tools/list":
            return response(msg.id, {"tools": json.loads(json.dumps(TOOLS)), "ttlMs": 60_000})
        if method == "tools/call":
            args = msg.params.get("arguments")
            args = args if isinstance(args, dict) else {}
            data = {"invoice_id": args.get("invoice_id", "INV-42"), "status": self.status}
            return response(msg.id, world_result(data))
        if method == "initialize":
            return response(
                msg.id,
                {
                    "protocolVersion": self.legacy_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "bank", "version": "1"},
                },
            )
        if method == "server/discover":
            return response(msg.id, {"capabilities": {"tools": {}}, "supportedVersions": [MODERN]})
        if method == "subscriptions/listen":
            await self.release.wait()
            return response(msg.id, {})
        return response(msg.id, {"echo": method})

    def last(self, method: str) -> Message:
        return [r for r in self.requests if r.method == method][-1]


def spec(name: str, tool: str = "get_invoice", on_call: int = 1, **params: Any) -> FaultSpec:
    return FaultSpec(name=FaultName(name), server="bank", tool=tool, on_call=on_call, params=params)


def make_plan(*faults: FaultSpec, **kw: Any) -> FaultPlan:
    return FaultPlan(run_id="run-1", server="bank", seed=7, faults=list(faults), **kw)


def tools_call(
    id: Any = 1,
    tool: str = "get_invoice",
    args: dict[str, Any] | None = None,
    *,
    modern: bool = True,
    meta: dict[str, Any] | None = None,
) -> Message:
    params: dict[str, Any] = {"name": tool, "arguments": args or {"invoice_id": "INV-42"}}
    m = dict(meta or {})
    if modern:
        m.update(modern_meta())
    if m:
        params["_meta"] = m
    return request(id, "tools/call", params)


def tools_list(id: Any = "l", *, modern: bool = True) -> Message:
    return request(id, "tools/list", {"_meta": modern_meta()} if modern else {})


def initialize(id: Any = 0) -> Message:
    return request(
        id,
        "initialize",
        {
            "protocolVersion": LEGACY,
            "capabilities": {},
            "clientInfo": {"name": "a", "version": "1"},
        },
    )


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "ledger-bank.jsonl"


# --------------------------------------------------------------------------- #
# Passthrough and recording
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_unknown_method_passthrough(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    up = FakeServer()
    msg = request(5, "resources/list", {"cursor": "x"})
    reply = await eng.handle_request(msg, up)
    assert up.requests[0].raw == msg.raw  # untouched (no trace in the plan)
    assert reply.message.raw == {"jsonrpc": "2.0", "id": 5, "result": {"echo": "resources/list"}}
    assert reply.before == [] and reply.negotiated_version is None
    (rec,) = read_ledger(ledger)
    e = rec.entry
    assert (e.method, e.tool, e.fault, e.request_id, e.seq) == ("resources/list", None, None, 5, 0)
    assert e.happened.forwarded == 1
    assert json.loads(e.told.text) == {"echo": "resources/list"}
    assert rec.trace_id is None and rec.span_id is None


@pytest.mark.anyio
async def test_passthrough_no_response_and_no_ledger():
    eng = ProxyEngine(make_plan(), None)
    up = FakeServer()
    up.none_for.add("ping")
    reply = await eng.handle_request(request("p", "ping"), up)
    assert reply.message is None
    assert eng.writer is None


@pytest.mark.anyio
async def test_tools_call_recorded(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    up = FakeServer()
    msg = tools_call(id="c1")
    reply = await eng.handle_request(msg, up)
    assert reply.message.result["structuredContent"]["status"] == "paid"
    (rec,) = read_ledger(ledger)
    e = rec.entry
    assert e.method == "tools/call" and e.tool == "get_invoice" and e.request_id == "c1"
    assert json.loads(e.asked.text) == {
        "name": "get_invoice",
        "arguments": {"invoice_id": "INV-42"},
    }
    assert "_meta" not in e.asked.text
    assert e.happened.forwarded == 1 and not e.happened.server_is_error
    assert json.loads(e.happened.server_result.text) == reply.message.result
    assert json.loads(e.told.text) == reply.message.result
    assert not e.told_is_error
    assert e.latency_ms >= 0
    assert e.protocol_version == MODERN
    assert eng.protocol_versions == {MODERN}
    assert rec.arguments == {"invoice_id": "INV-42"}
    assert eng.call_counts["get_invoice"] == 1 and eng.completed_calls == 1


@pytest.mark.anyio
async def test_tools_call_odd_arguments_and_id(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    msg = request([1], "tools/call", {"name": "get_invoice", "arguments": "nope"})
    await eng.handle_request(msg, FakeServer())
    (rec,) = read_ledger(ledger)
    assert rec.entry.request_id is None  # not a str/int id
    assert rec.entry.protocol_version is None


@pytest.mark.anyio
async def test_faulted_call_recorded(ledger):
    eng = ProxyEngine(make_plan(spec("lost_response")), ledger)
    up = FakeServer()
    reply = await eng.handle_request(tools_call(), up)
    assert reply.message.result["isError"] is True
    assert reply.message.result["resultType"] == "complete"  # modern client
    (rec,) = read_ledger(ledger)
    assert rec.entry.fault is FaultName.LOST_RESPONSE
    assert rec.entry.told_is_error and not rec.entry.happened.server_is_error
    assert "lost_response" in rec.entry.happened.note


@pytest.mark.anyio
async def test_duplicate_and_wire_recorded(ledger):
    eng = ProxyEngine(
        make_plan(spec("duplicate_delivery"), spec("malformed", "send_email", mode="wire")), ledger
    )
    up = FakeServer()
    await eng.handle_request(tools_call(), up)
    reply = await eng.handle_request(tools_call(2, "send_email"), up)
    assert isinstance(reply.message, bytes)
    dup, wire = read_ledger(ledger)
    assert dup.entry.happened.forwarded == 2
    assert dup.duplicate_result is not None and not dup.duplicate_is_error
    assert wire.entry.told_is_error
    assert wire.entry.told.sha256 and wire.entry.told.size == len(reply.message)


@pytest.mark.anyio
async def test_sleep_is_passed_to_faults(ledger):
    delays: list[float] = []

    async def sleep(s: float) -> None:
        delays.append(s)

    eng = ProxyEngine(make_plan(spec("slow", slow_ms=4000)), ledger, sleep=sleep)
    reply = await eng.handle_request(tools_call(), FakeServer())
    assert delays == [4.0]
    assert reply.message.result["structuredContent"]["status"] == "paid"


@pytest.mark.anyio
async def test_server_override_selects_faults(ledger):
    plan = FaultPlan(
        server="bank",
        faults=[spec("error"), FaultSpec(name=FaultName.ERROR, server="shop", tool="get_invoice")],
    )
    eng = ProxyEngine(plan, ledger, server="shop")
    assert eng.server == "shop" and len(eng.faults) == 1
    await eng.handle_request(tools_call(), FakeServer())
    assert read_ledger(ledger)[0].entry.server == "shop"


# --------------------------------------------------------------------------- #
# Protocol versions
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_version_from_header(ledger):
    eng = ProxyEngine(make_plan(spec("error")), ledger)
    reply = await eng.handle_request(tools_call(modern=False), FakeServer(), header_version=MODERN)
    assert read_ledger(ledger)[0].entry.protocol_version == MODERN
    assert reply.message.result["resultType"] == "complete"


@pytest.mark.anyio
async def test_meta_version_beats_header(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    await eng.handle_request(tools_call(), FakeServer(), header_version="2027-01-01")
    assert read_ledger(ledger)[0].entry.protocol_version == MODERN


@pytest.mark.anyio
async def test_legacy_initialize_sets_session_version(ledger):
    eng = ProxyEngine(make_plan(spec("error")), ledger)
    up = FakeServer()
    reply = await eng.handle_request(initialize(), up, session="s1")
    assert reply.negotiated_version == LEGACY
    assert eng.session_versions == {"s1": LEGACY}
    assert reply.message.result["capabilities"] == {"tools": {}}  # no rug pull planned
    r2 = await eng.handle_request(tools_call(modern=False), up, session="s1")
    assert "resultType" not in r2.message.result  # legacy era
    other = await eng.handle_request(tools_call(2, modern=False), up, session="s2")
    init, call1, call2 = read_ledger(ledger)
    assert init.entry.method == "initialize" and init.entry.protocol_version == LEGACY
    assert call1.entry.protocol_version == LEGACY
    assert call2.entry.protocol_version is None  # unknown session
    assert other.message is not None
    assert eng.protocol_versions == {LEGACY}


@pytest.mark.anyio
async def test_initialize_without_version(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    up = FakeServer()
    up.legacy_version = None  # type: ignore[assignment]
    reply = await eng.handle_request(initialize(), up)
    assert reply.negotiated_version is None and eng.session_versions == {}


# --------------------------------------------------------------------------- #
# Tracing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_traceparent_injected(ledger):
    eng = ProxyEngine(make_plan(traceparent=TRACE), ledger)
    up = FakeServer()
    msg = tools_call()
    await eng.handle_request(msg, up)
    sent = up.last("tools/call")
    tp = sent.params["_meta"][META_TRACEPARENT]
    trace = parse_traceparent(tp)
    assert trace is not None and trace[0] == "a" * 32 and trace[1] != "b" * 16
    assert sent.params["_meta"][META_PROTOCOL_VERSION] == MODERN  # other meta kept
    assert META_TRACEPARENT not in msg.params["_meta"]  # original not mutated
    (rec,) = read_ledger(ledger)
    assert (rec.trace_id, rec.span_id, rec.parent_span_id) == ("a" * 32, trace[1], "b" * 16)
    assert "_meta" not in rec.entry.asked.text


@pytest.mark.anyio
async def test_traceparent_added_without_params(ledger):
    eng = ProxyEngine(make_plan(traceparent=TRACE), ledger)
    up = FakeServer()
    await eng.handle_request(request(1, "ping"), up)
    assert parse_traceparent(up.last("ping").params["_meta"][META_TRACEPARENT])[0] == "a" * 32


@pytest.mark.anyio
async def test_client_traceparent_kept(ledger):
    eng = ProxyEngine(make_plan(traceparent=TRACE), ledger)
    up = FakeServer()
    own = "00-" + "c" * 32 + "-" + "d" * 16 + "-01"
    msg = tools_call(meta={META_TRACEPARENT: own})
    await eng.handle_request(msg, up)
    assert up.last("tools/call").raw == msg.raw
    (rec,) = read_ledger(ledger)
    assert (rec.trace_id, rec.span_id, rec.parent_span_id) == ("c" * 32, "d" * 16, None)


@pytest.mark.anyio
async def test_traceparent_skipped_for_list_params(ledger):
    eng = ProxyEngine(make_plan(traceparent=TRACE), ledger)
    up = FakeServer()
    msg = Message({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1, 2]})
    await eng.handle_request(msg, up)
    assert up.last("ping").raw == msg.raw
    assert read_ledger(ledger)[0].trace_id is None


def test_bad_plan_traceparent():
    eng = ProxyEngine(make_plan(traceparent="garbage"), None)
    assert eng.trace_id is None and eng.parent_span_id is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("00-abc", None),
        ("00-" + "a" * 31 + "-" + "b" * 16 + "-01", None),
        ("00-" + "a" * 32 + "-" + "b" * 15 + "-01", None),
        ("00-" + "a" * 32 + "-" + "b" * 16 + "-01-extra", None),
        (TRACE, ("a" * 32, "b" * 16)),
    ],
)
def test_parse_traceparent(value, expected):
    assert parse_traceparent(value) == expected


# --------------------------------------------------------------------------- #
# listChanged advertising and tools/list poisoning
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["initialize", "server/discover"])
async def test_list_changed_advertised_for_rug_pull(ledger, method):
    rug = spec("description_poison", "send_email", mode="rug_pull")
    up = FakeServer()
    msg = initialize() if method == "initialize" else request(1, method, {"_meta": modern_meta()})
    reply = await ProxyEngine(make_plan(rug), ledger).handle_request(msg, up)
    assert reply.message.result["capabilities"]["tools"]["listChanged"] is True
    static = spec("description_poison", "send_email")
    plain = await ProxyEngine(make_plan(static), None).handle_request(msg, up)
    assert "listChanged" not in plain.message.result["capabilities"]["tools"]
    # the ledger records what the client was told
    told = json.loads(read_ledger(ledger)[0].entry.told.text)
    assert told["capabilities"]["tools"]["listChanged"] is True


@pytest.mark.anyio
async def test_list_changed_creates_capabilities(ledger):
    class NoCaps(FakeServer):
        async def send(self, msg):
            return response(msg.id, {"supportedVersions": [MODERN]})

    rug = spec("description_poison", "send_email", mode="rug_pull")
    reply = await ProxyEngine(make_plan(rug), None).handle_request(
        request(1, "server/discover"), NoCaps()
    )
    assert reply.message.result["capabilities"] == {"tools": {"listChanged": True}}


@pytest.mark.anyio
async def test_tools_list_poisoned_and_recorded(ledger):
    eng = ProxyEngine(make_plan(spec("description_poison", "send_email")), ledger)
    reply = await eng.handle_request(tools_list(), FakeServer())
    tools = {t["name"]: t for t in reply.message.result["tools"]}
    canary = eng.faults[0].ctx.canary
    assert canary.lower() in tools["send_email"]["description"]
    assert tools["get_invoice"]["description"] == "Look up an invoice."
    (rec,) = read_ledger(ledger)
    assert rec.entry.method == "tools/list"
    assert rec.entry.fault is FaultName.DESCRIPTION_POISON
    assert rec.canaries == [canary]
    assert rec.entry.happened.note == "poisoned tool description served"
    assert "<IMPORTANT>" not in rec.entry.happened.server_result.text
    assert set(eng.known_tools) == {"get_invoice", "send_email"}
    assert eng.tools_lists == 1


@pytest.mark.anyio
async def test_tools_list_clean(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    up = FakeServer()
    reply = await eng.handle_request(tools_list(), up)
    assert reply.message.result["ttlMs"] == 60_000
    (rec,) = read_ledger(ledger)
    assert rec.entry.fault is None and rec.canaries == [] and rec.entry.happened.note is None
    up.none_for.add("tools/list")
    reply = await eng.handle_request(tools_list(), up)
    assert reply.message is None and eng.tools_lists == 2


# --------------------------------------------------------------------------- #
# Rug pull
# --------------------------------------------------------------------------- #


def rug_plan(on_call: int = 2) -> FaultPlan:
    return make_plan(spec("description_poison", "send_email", on_call=on_call, mode="rug_pull"))


def send_email_desc(reply) -> str:
    return {t["name"]: t for t in reply.message.result["tools"]}["send_email"]["description"]


@pytest.mark.anyio
async def test_rug_pull_legacy_session(ledger):
    eng = ProxyEngine(rug_plan(), ledger)
    up = FakeServer()
    await eng.handle_request(initialize(), up)
    lst = await eng.handle_request(tools_list(modern=False), up)
    assert "<IMPORTANT>" not in send_email_desc(lst)
    assert lst.message.result["ttlMs"] == 1
    r1 = await eng.handle_request(tools_call(1, modern=False), up)
    assert r1.before == []
    r2 = await eng.handle_request(tools_call(2, modern=False), up)
    assert [m.raw for m in r2.before] == [notification(TOOLS_LIST_CHANGED).raw]
    lst2 = await eng.handle_request(tools_list(modern=False), up)
    assert "<IMPORTANT>" in send_email_desc(lst2)
    r3 = await eng.handle_request(tools_call(3, modern=False), up)
    assert r3.before == []  # pulled once only
    recs = read_ledger(ledger)
    events = [r for r in recs if r.event == "rug_pull"]
    assert len(events) == 1
    ev = events[0].entry
    assert ev.method == TOOLS_LIST_CHANGED and ev.tool == "send_email"
    assert ev.fault is FaultName.DESCRIPTION_POISON and ev.happened.forwarded == 0
    assert "list_changed sent to the session" in ev.happened.note
    assert ev.told is not None
    assert [r.entry.seq for r in recs] == list(range(len(recs)))
    poisoned = [r for r in recs if r.entry.method == "tools/list" and r.canaries]
    assert len(poisoned) == 1


@pytest.mark.anyio
async def test_rug_pull_needs_a_tools_list(ledger):
    eng = ProxyEngine(rug_plan(on_call=1), ledger)
    up = FakeServer()
    await eng.handle_request(initialize(), up)
    r = await eng.handle_request(tools_call(modern=False), up)
    assert r.before == [] and not eng.rug_pulled
    await eng.handle_request(tools_list(modern=False), up)
    r = await eng.handle_request(tools_call(2, modern=False), up)
    assert len(r.before) == 1


@pytest.mark.anyio
async def test_rug_pull_modern_without_listener(ledger):
    eng = ProxyEngine(rug_plan(on_call=1), ledger)
    up = FakeServer()
    await eng.handle_request(tools_list(), up)
    r = await eng.handle_request(tools_call(), up)
    assert r.before == []  # no legacy session to notify
    ev = next(r for r in read_ledger(ledger) if r.event == "rug_pull")
    assert ev.entry.told is None
    assert ev.entry.happened.note.endswith("(short ttlMs on tool lists)")


@pytest.mark.anyio
async def test_rug_pull_subscription_listener(ledger):
    eng = ProxyEngine(rug_plan(on_call=1), ledger)
    up = FakeServer()
    got: list[Message] = []
    other: list[Message] = []

    async def emit(m: Message) -> None:
        got.append(m)

    async def emit_other(m: Message) -> None:
        other.append(m)

    replies: list[Any] = []

    async def listen(id: Any, wants: bool, sink) -> None:
        params = {"notifications": {"toolsListChanged": wants}, "_meta": modern_meta()}
        replies.append(
            await eng.handle_request(request(id, "subscriptions/listen", params), up, emit=sink)
        )

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(listen, "sub-1", True, emit)
            tg.start_soon(listen, "sub-2", False, emit_other)
            while len(eng.listeners) < 2:
                await anyio.sleep(0)
            await eng.handle_request(tools_list(), up)
            r = await eng.handle_request(tools_call(), up)
            assert r.before == []
            up.release.set()
    assert len(got) == 1 and other == []
    n = got[0]
    assert n.method == TOOLS_LIST_CHANGED
    assert n.params["_meta"][META_SUBSCRIPTION_ID] == "sub-1"
    assert eng.listeners == []  # removed once the stream ended
    assert all(rep.message.result == {} for rep in replies)
    recs = read_ledger(ledger)
    opened = [r for r in recs if r.entry.method == "subscriptions/listen"]
    assert len(opened) == 2 and opened[0].entry.happened.note == "change stream opened"
    ev = next(r for r in recs if r.event == "rug_pull")
    assert "sent on 1 subscription stream(s)" in ev.entry.happened.note


@pytest.mark.anyio
async def test_listen_without_emit(ledger):
    eng = ProxyEngine(make_plan(), ledger)
    up = FakeServer()
    up.release.set()
    reply = await eng.handle_request(request(1, "subscriptions/listen", {}), up)
    assert reply.message.result == {} and eng.listeners == []


# --------------------------------------------------------------------------- #
# State rebuilt from the ledger
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_call_counter_continues_across_engines(ledger):
    plan = make_plan(spec("error", on_call=2))
    up = FakeServer()
    first = await ProxyEngine(plan, ledger).handle_request(tools_call(1), up)
    assert first.message.result["isError"] is False
    eng2 = ProxyEngine(plan, ledger)
    assert eng2.call_counts["get_invoice"] == 1 and eng2.completed_calls == 1
    second = await eng2.handle_request(tools_call(2), up)
    assert second.message.result["isError"] is True
    recs = read_ledger(ledger)
    assert [r.entry.seq for r in recs] == [0, 1]
    assert recs[1].entry.fault is FaultName.ERROR
    assert len([r for r in up.requests if r.method == "tools/call"]) == 1


@pytest.mark.anyio
async def test_rug_pull_not_repeated_after_restart(ledger):
    up = FakeServer()
    eng = ProxyEngine(rug_plan(on_call=1), ledger)
    await eng.handle_request(initialize(), up)
    await eng.handle_request(tools_list(modern=False), up)
    r = await eng.handle_request(tools_call(modern=False), up)
    assert len(r.before) == 1
    eng2 = ProxyEngine(rug_plan(on_call=1), ledger)
    assert eng2.rug_pulled == {"description_poison:send_email"}
    assert eng2.tools_lists == 1 and set(eng2.known_tools) == {"get_invoice", "send_email"}
    await eng2.handle_request(initialize(1), up)
    lst = await eng2.handle_request(tools_list(modern=False), up)
    assert "<IMPORTANT>" in send_email_desc(lst)  # already pulled: poisoned straight away
    r = await eng2.handle_request(tools_call(2, modern=False), up)
    assert r.before == []
    assert len([x for x in read_ledger(ledger) if x.event == "rug_pull"]) == 1


@pytest.mark.anyio
async def test_history_rebuilt_for_stale(ledger):
    up = FakeServer(status="open")
    await ProxyEngine(make_plan(), ledger).handle_request(tools_call(1), up)
    up.status = "paid"
    eng2 = ProxyEngine(make_plan(spec("stale", on_call=2)), ledger)
    key = '{"invoice_id":"INV-42"}'
    assert eng2.history("get_invoice", key)[0]["structuredContent"]["status"] == "open"
    reply = await eng2.handle_request(tools_call(2), up)
    assert reply.message.result["structuredContent"]["status"] == "open"
    rec = read_ledger(ledger)[-1]
    assert rec.entry.happened.note == "stale: re-served an older result for the same call"
    assert (
        json.loads(rec.entry.happened.server_result.text)["structuredContent"]["status"] == "paid"
    )
    # the new server result joins the history too
    assert len(eng2.history("get_invoice", key)) == 2


def test_load_ignores_other_servers_and_bad_payloads(ledger):
    from polygraph.ledger import LedgerWriter

    w = LedgerWriter(ledger)
    w.write(_record(seq=40, server="shop", method="tools/call", tool="get_invoice"))
    w.write(_record(seq=3, method="tools/call", tool="get_invoice", server_result="not json"))
    w.write(_record(seq=4, method="tools/list", server_result="[1]"))
    eng = ProxyEngine(make_plan(), ledger)
    assert eng.call_counts["get_invoice"] == 1
    assert eng.history("get_invoice", '{"invoice_id":"INV-42"}') == []
    assert eng.tools_lists == 1 and eng.known_tools == {}
    assert eng._seq == 5


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["notification", "transport"])
async def test_cancel_stops_timeout_hold(ledger, how):
    eng = ProxyEngine(make_plan(spec("timeout", timeout_s=30)), ledger)
    up = FakeServer()
    replies = []

    async def run() -> None:
        replies.append(await eng.handle_request(tools_call(7), up))

    t0 = time.monotonic()
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            while not eng._cancel_events:
                await anyio.sleep(0)
            eng.on_client_notification(notification("notifications/progress", {"requestId": 7}))
            if how == "notification":
                eng.on_client_notification(notification(CANCELLED, {"requestId": 7}))
            else:
                eng.cancel(7)
    assert time.monotonic() - t0 < 2
    assert replies[0].message is None
    (rec,) = read_ledger(ledger)
    assert rec.entry.told is None and "cancelled" in rec.entry.happened.note
    assert [r for r in up.requests if r.method == "tools/call"] == []


@pytest.mark.anyio
async def test_cancel_before_call_and_id_types(ledger):
    eng = ProxyEngine(make_plan(spec("timeout", timeout_s=0.05, times=0)), ledger)
    up = FakeServer()
    eng.cancel(1)
    with anyio.fail_after(2):
        r1 = await eng.handle_request(tools_call(1), up)
        # "1" (a string) is a different request from 1
        r2 = await eng.handle_request(tools_call("1"), up)
    assert r1.message is None
    assert r2.message.error["code"] == -32001


# --------------------------------------------------------------------------- #
# Replay cassette
# --------------------------------------------------------------------------- #


def _record(
    *,
    seq: int = 0,
    server: str = "bank",
    method: str = "tools/call",
    tool: str | None = "get_invoice",
    params: dict[str, Any] | None = None,
    told: Any = None,
    server_result: str | None = None,
) -> LedgerRecord:
    params = (
        {"name": "get_invoice", "arguments": {"invoice_id": "INV-42"}} if params is None else params
    )
    from datetime import UTC, datetime

    return LedgerRecord(
        entry=LedgerEntry(
            seq=seq,
            ts=datetime(2026, 10, 13, tzinfo=UTC),
            server=server,
            method=method,
            tool=tool,
            asked=Payload.of(strip_meta(params)),
            happened=Happened(
                forwarded=1,
                server_result=Payload.of(server_result) if server_result is not None else None,
            ),
            told=Payload.of(told) if told is not None else None,
        )
    )


def write_cassette(path, records: list[LedgerRecord], extra_lines: list[str] = ()) -> str:
    lines = [json.dumps({"cassette": 1, "test_id": "tests/test_x.py::test_pay", "seed": 7})]
    lines += list(extra_lines)
    lines += [r.model_dump_json() for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


@pytest.mark.anyio
async def test_replay_cassette(tmp_path, ledger):
    first = {"content": [{"type": "text", "text": "recorded first"}], "isError": False}
    second = {"error": {"code": -32000, "message": "Connection closed"}}
    other_args = {"name": "get_invoice", "arguments": {"invoice_id": "INV-7"}}
    path = write_cassette(
        tmp_path / "cassette.jsonl",
        [
            _record(told=first),
            _record(server="shop", told={"content": [], "isError": False}),
            _record(tool="get_invoice", told=None),  # never answered: skipped
            _record(told=second),
            _record(params=other_args, told={"content": [{"type": "text", "text": "seven"}]}),
        ],
        extra_lines=["not json", '{"entry": {"bad": 1}}', "[1, 2]"],
    )
    eng = ProxyEngine(make_plan(replay_cassette=path, traceparent=TRACE), ledger)
    up = FakeServer()
    r1 = await eng.handle_request(tools_call(1), up)
    assert tool_text(r1.message.result) == "recorded first" and r1.message.id == 1
    r2 = await eng.handle_request(tools_call(2), up)
    assert r2.message.error == {"code": -32000, "message": "Connection closed"}
    assert r2.message.id == 2
    r3 = await eng.handle_request(tools_call(3), up)  # cassette exhausted: live answer
    assert r3.message.result["structuredContent"]["status"] == "paid"
    r4 = await eng.handle_request(tools_call(4, args={"invoice_id": "INV-7"}), up)
    assert tool_text(r4.message.result) == "seven"
    # the server still ran every call
    assert len([r for r in up.requests if r.method == "tools/call"]) == 4
    recs = read_ledger(ledger)
    assert tool_text(json.loads(recs[0].entry.told.text)) == "recorded first"
    assert recs[1].entry.told_is_error


@pytest.mark.anyio
async def test_replay_skips_unusable_payloads(tmp_path):
    rec = _record(told={"x": 1})
    rec.entry.told = rec.entry.told.without_text()
    path = write_cassette(tmp_path / "c.jsonl", [rec])
    eng = ProxyEngine(make_plan(replay_cassette=path), None)
    reply = await eng.handle_request(tools_call(), FakeServer())
    assert reply.message.result["structuredContent"]["status"] == "paid"


@pytest.mark.anyio
async def test_replay_missing_cassette_file(tmp_path):
    eng = ProxyEngine(make_plan(replay_cassette=str(tmp_path / "missing.jsonl")), None)
    reply = await eng.handle_request(tools_call(), FakeServer())
    assert reply.message.result["structuredContent"]["status"] == "paid"


def test_message_from_payload():
    assert message_from_payload(1, Payload.of("x", include_text=False)) is None
    assert message_from_payload(1, Payload.of("{not json")) is None
    err = message_from_payload(1, Payload.of({"error": {"code": -1, "message": "m"}}))
    assert err.error == {"code": -1, "message": "m"} and err.id == 1
    not_err = message_from_payload(2, Payload.of({"error": "text", "x": 1}))
    assert not_err.result == {"error": "text", "x": 1}
    s = message_from_payload(3, Payload.of({"error": "just a string"}))
    assert s.result == {"error": "just a string"}
    lst = message_from_payload(4, Payload.of([1, 2]))
    assert lst.result == [1, 2] and lst.is_response


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_ledger_write_failure_does_not_raise(tmp_path, caplog):
    path = tmp_path / "ledger.jsonl"
    eng = ProxyEngine(make_plan(spec("error")), path)
    path.mkdir()  # the ledger "file" is now a directory: every write fails
    reply = await eng.handle_request(tools_call(), FakeServer())
    assert reply.message.result["isError"] is True
    assert "could not write ledger" in caplog.text


@pytest.mark.anyio
async def test_error_response_from_server(ledger):
    class Broken(FakeServer):
        async def send(self, msg):
            return error_response(msg.id, -32603, "boom")

    eng = ProxyEngine(make_plan(), ledger)
    reply = await eng.handle_request(tools_call(), Broken())
    assert reply.message.error["code"] == -32603
    (rec,) = read_ledger(ledger)
    assert rec.entry.happened.server_is_error and rec.entry.told_is_error
    assert json.loads(rec.entry.told.text) == {"error": {"code": -32603, "message": "boom"}}
    assert eng.history("get_invoice", '{"invoice_id":"INV-42"}') == []
