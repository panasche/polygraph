"""JSON-RPC messages, helper constructors and the SSE framing."""

from __future__ import annotations

import json

import pytest

from polygraph import jsonrpc
from polygraph.jsonrpc import (
    SSE_KEEPALIVE,
    Kind,
    Message,
    ParseError,
    SseEvent,
    SseParser,
    encode_sse,
    encode_sse_message,
    error_response,
    id_key,
    notification,
    request,
    response,
)

# --------------------------------------------------------------------------- #
# Message
# --------------------------------------------------------------------------- #


def test_kinds():
    assert Message({"jsonrpc": "2.0", "id": 1, "method": "ping"}).kind is Kind.REQUEST
    assert Message({"jsonrpc": "2.0", "method": "notifications/x"}).kind is Kind.NOTIFICATION
    assert Message({"jsonrpc": "2.0", "id": 1, "result": {}}).kind is Kind.RESPONSE
    assert Message({"jsonrpc": "2.0", "id": 1, "error": {"code": 1}}).kind is Kind.RESPONSE
    # a request with id None is still a request (the key is present)
    assert Message({"jsonrpc": "2.0", "id": None, "method": "m"}).is_request


def test_not_a_message():
    with pytest.raises(ParseError):
        Message({"jsonrpc": "2.0", "id": 1})


@pytest.mark.parametrize("data", [b"{not json", "[1, 2]", b"\xff\xfe\x00", '"text"'])
def test_parse_errors(data):
    with pytest.raises(ParseError):
        Message.parse(data)


def test_parse_inputs():
    raw = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
    assert Message.parse(raw).raw is raw
    assert Message.parse(json.dumps(raw)).raw == raw
    assert Message.parse(json.dumps(raw).encode()).raw == raw


def test_accessors_request():
    m = request(3, "tools/call", {"name": "get_invoice", "arguments": {"id": "INV-42"}})
    assert m.id == 3
    assert m.method == "tools/call"
    assert m.params == {"name": "get_invoice", "arguments": {"id": "INV-42"}}
    assert m.result is None and m.error is None
    assert m.is_request and not m.is_notification and not m.is_response


def test_accessors_tolerate_odd_shapes():
    m = Message({"jsonrpc": "2.0", "id": 1, "method": 5, "params": [1, 2]})
    assert m.method is None
    assert m.params == {}
    r = Message({"jsonrpc": "2.0", "id": 1, "error": "boom"})
    assert r.error is None and r.is_response


def test_accessors_response():
    ok = response("a", {"x": 1})
    assert ok.result == {"x": 1} and ok.error is None and ok.is_response
    err = error_response("a", -32000, "Connection closed")
    assert err.error == {"code": -32000, "message": "Connection closed"}
    err2 = error_response(None, -32602, "bad", data={"field": "id"})
    assert err2.error == {"code": -32602, "message": "bad", "data": {"field": "id"}}


def test_helpers_without_params():
    assert "params" not in request(1, "ping").raw
    n = notification("notifications/initialized")
    assert n.is_notification and "params" not in n.raw and "id" not in n.raw
    n2 = notification("notifications/cancelled", {"requestId": 1})
    assert n2.params == {"requestId": 1}
    assert request(1, "m", {}).raw["params"] == {}


def test_to_json_and_line_keep_unicode():
    m = response(1, {"text": "Customer A paid ₹500"})
    j = m.to_json()
    assert "₹" in j and " " not in j.replace("Customer A paid ₹500", "")
    line = m.to_line()
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert Message.parse(line).raw == m.raw


def test_copy_is_deep():
    m = request(1, "tools/call", {"arguments": {"items": [1, 2]}})
    c = m.copy()
    c.raw["params"]["arguments"]["items"].append(3)
    c.raw["id"] = 2
    assert m.raw["params"]["arguments"]["items"] == [1, 2]
    assert m.id == 1
    assert c.kind is Kind.REQUEST


def test_id_key_keeps_types_apart():
    assert id_key(1) != id_key("1")
    assert id_key(1) == id_key(1)
    assert id_key(None) == "null"
    assert len({id_key(1), id_key("1"), id_key(1.5), id_key(None)}) == 4


def test_error_code_constants():
    assert jsonrpc.REQUEST_TIMEOUT == -32001
    assert jsonrpc.CONNECTION_LOST == -32000
    assert jsonrpc.PARSE_ERROR == -32700


# --------------------------------------------------------------------------- #
# SSE encode
# --------------------------------------------------------------------------- #


def test_encode_sse():
    assert encode_sse("x") == b"event: message\ndata: x\n\n"
    assert encode_sse("a\nb", event=None) == b"data: a\ndata: b\n\n"
    assert encode_sse("", event="") == b"data: \n\n"


def test_encode_sse_message_round_trip():
    m = response(1, {"text": "line1\nline2", "who": "Customer A"})
    data = encode_sse_message(m)
    events = list(SseParser().feed(data))
    assert events == [SseEvent("message", m.to_json())]
    assert Message.parse(events[0].data).raw == m.raw


# --------------------------------------------------------------------------- #
# SSE parse
# --------------------------------------------------------------------------- #


def _all(chunks):
    p = SseParser()
    out = []
    for c in chunks:
        out.extend(p.feed(c))
    out.extend(p.flush())
    return out


@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r"])
def test_line_endings(eol):
    data = eol.join([b"event: update", b"data: one", b"data: two", b"", b"data: three", b"", b""])
    assert _all([data]) == [SseEvent("update", "one\ntwo"), SseEvent("message", "three")]


def test_comments_and_keepalives():
    data = SSE_KEEPALIVE + b": ping\n\ndata: x\n: mid comment\ndata: y\n\n" + SSE_KEEPALIVE
    assert _all([data]) == [SseEvent("message", "x\ny")]


def test_event_name_reset_by_blank_line_without_data():
    # an event line with no data is dropped; its name must not leak into the next event
    assert _all([b"event: lonely\n\ndata: z\n\n"]) == [SseEvent("message", "z")]


def test_field_parsing_details():
    data = b"data:nospace\ndata:  two spaces\nid: 5\nretry: 10\nfield-only\n\n"
    assert _all([data]) == [SseEvent("message", "nospace\n two spaces")]


def test_empty_data_line_counts():
    assert _all([b"data\n\n"]) == [SseEvent("message", "")]


def test_flush_trailing_event_without_blank_line():
    p = SseParser()
    assert list(p.feed(b"event: e\ndata: tail")) == []
    assert list(p.flush()) == [SseEvent("e", "tail")]
    # flushed state is clean
    assert list(p.flush()) == []


def test_flush_with_complete_lines_but_no_terminator():
    p = SseParser()
    assert list(p.feed(b"data: a\n")) == []
    assert list(p.flush()) == [SseEvent("message", "a")]


def test_flush_nothing():
    assert list(SseParser().flush()) == []


def test_utf8_split_across_chunks():
    data = encode_sse('{"who":"Customer ₹"}')
    p = SseParser()
    out = []
    for i in range(len(data)):
        out.extend(p.feed(data[i : i + 1]))
    assert out == [SseEvent("message", '{"who":"Customer ₹"}')]


@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r"])
def test_chunk_boundaries_anywhere(eol):
    data = eol.join(
        [
            b": keep-alive",
            b"",
            b"event: message",
            b'data: {"a":1}',
            b"data: more",
            b"",
            b"data: second",
            b"",
            b"",
        ]
    )
    expected = [SseEvent("message", '{"a":1}\nmore'), SseEvent("message", "second")]
    assert _all([data]) == expected
    for cut in range(len(data) + 1):
        assert _all([data[:cut], data[cut:]]) == expected, cut
    # every byte on its own
    assert _all([data[i : i + 1] for i in range(len(data))]) == expected


def test_crlf_split_between_chunks_after_data_line():
    # "\r" at the end of one chunk and "\n" at the start of the next is ONE line end
    p = SseParser()
    out = list(p.feed(b"data: a\r"))
    out += list(p.feed(b"\ndata: b\r\n\r\n"))
    assert out == [SseEvent("message", "a\nb")]


def test_lone_cr_then_empty_chunk_then_data():
    p = SseParser()
    out = list(p.feed(b"data: a\r"))
    out += list(p.feed(b""))
    out += list(p.feed(b"data: b\r\r"))
    assert out == [SseEvent("message", "a\nb")]
