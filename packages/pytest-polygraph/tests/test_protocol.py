"""MCP protocol helpers: version eras, _meta, tool results, header encoding."""

from __future__ import annotations

import base64

import pytest

from polygraph import protocol as p
from polygraph.jsonrpc import error_response, request, response


@pytest.mark.parametrize("version", p.LEGACY_VERSIONS)
def test_legacy_versions_are_not_modern(version):
    assert not p.is_modern(version)


@pytest.mark.parametrize(
    ("version", "modern"),
    [
        ("2026-07-28", True),
        ("2027-01-01", True),  # unknown future version counts as modern
        ("2025-12-01", True),  # after the last legacy version
        ("2024-01-01", False),  # unknown older version
        ("", False),
        (None, False),
    ],
)
def test_is_modern(version, modern):
    assert p.is_modern(version) is modern


def test_version_tables():
    assert p.LATEST_MODERN_VERSION in p.MODERN_VERSIONS
    assert p.LATEST_LEGACY_VERSION in p.LEGACY_VERSIONS
    assert set(p.KNOWN_VERSIONS) == set(p.LEGACY_VERSIONS) | set(p.MODERN_VERSIONS)
    assert {-32020, -32021, -32022} == p.MODERN_ERROR_CODES


def test_meta_of():
    assert p.meta_of(None) == {}
    assert p.meta_of({}) == {}
    assert p.meta_of({"_meta": "nope"}) == {}
    assert p.meta_of({"_meta": {"a": 1}}) == {"a": 1}
    assert p.meta_of([1]) == {}  # type: ignore[arg-type]


def test_modern_meta():
    m = p.modern_meta()
    assert m[p.META_PROTOCOL_VERSION] == p.LATEST_MODERN_VERSION
    assert m[p.META_CLIENT_INFO] == {"name": "polygraph", "version": "0"}
    assert m[p.META_CLIENT_CAPABILITIES] == {}
    m2 = p.modern_meta(
        "2027-01-01", client_name="agent", client_version="1.2", capabilities={"x": {}}
    )
    assert m2[p.META_PROTOCOL_VERSION] == "2027-01-01"
    assert m2[p.META_CLIENT_INFO] == {"name": "agent", "version": "1.2"}
    assert m2[p.META_CLIENT_CAPABILITIES] == {"x": {}}


def test_request_version():
    assert p.request_version(request(1, "tools/list", {"_meta": p.modern_meta()})) == "2026-07-28"
    assert p.request_version(request(1, "tools/list")) is None
    assert p.request_version(request(1, "x", {"_meta": {p.META_PROTOCOL_VERSION: 5}})) is None


def test_strip_meta():
    params = {"name": "t", "arguments": {"a": 1}, "_meta": {"k": "v"}}
    assert p.strip_meta(params) == {"name": "t", "arguments": {"a": 1}}
    assert "_meta" in params  # not mutated
    assert p.strip_meta(None) == {}
    assert p.strip_meta("x") == {}  # type: ignore[arg-type]


def test_tool_text():
    res = {
        "content": [
            {"type": "text", "text": "one"},
            {"type": "image", "data": "..."},
            "junk",
            {"type": "text", "text": 2},
            {"type": "text"},
        ]
    }
    assert p.tool_text(res) == "one\n2\n"
    assert p.tool_text({"content": None}) == ""
    assert p.tool_text("not a dict") == ""
    assert p.tool_text({}) == ""


def test_tool_result():
    legacy = p.tool_result("ok")
    assert legacy == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    modern = p.tool_result("bad", is_error=True, structured={"a": 1}, modern=True)
    assert modern["resultType"] == "complete"
    assert modern["isError"] is True
    assert modern["structuredContent"] == {"a": 1}
    assert "resultType" not in p.tool_result("x", structured={})
    assert p.tool_result("x", structured={})["structuredContent"] == {}


def test_is_error_response():
    assert not p.is_error_response(None)
    assert p.is_error_response(error_response(1, -32000, "x"))
    assert p.is_error_response(response(1, p.tool_result("x", is_error=True)))
    assert not p.is_error_response(response(1, p.tool_result("x")))
    assert not p.is_error_response(response(1, ["not", "a", "dict"]))


@pytest.mark.parametrize("value", ["get_invoice", "INV-42", "a b", "~!@#"])
def test_header_value_plain(value):
    assert p.header_value(value) == value
    assert p.decode_header_value(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "Customer ₹",  # non-ASCII
        " padded",
        "padded ",
        "tab\there",
        "new\nline",
        "=?base64?abc?=",  # looks like the sentinel; must be encoded so it round-trips
        "",
    ],
)
def test_header_value_encoded_round_trip(value):
    enc = p.header_value(value)
    if value == "":
        assert enc == ""  # empty is safe as-is
    else:
        assert enc.startswith("=?base64?") and enc.endswith("?=")
        assert all(0x20 <= ord(c) <= 0x7E for c in enc)
    assert p.decode_header_value(enc) == value


def test_decode_header_value_sentinel():
    enc = "=?base64?" + base64.b64encode("Café".encode()).decode() + "?="
    assert p.decode_header_value(enc) == "Café"
    assert p.decode_header_value("=?base64?") == "=?base64?"  # no closing sentinel


def test_canonical_json():
    assert p.canonical_json({"b": 1, "a": [2, {"d": 1, "c": 0}]}) == '{"a":[2,{"c":0,"d":1}],"b":1}'
    assert p.canonical_json({"x": "₹"}) == '{"x":"₹"}'
    assert p.canonical_json({"s": {1, 2}}).startswith('{"s":"')  # default=str fallback
    assert p.canonical_json({"a": 1, "b": 2}) == p.canonical_json({"b": 2, "a": 1})
