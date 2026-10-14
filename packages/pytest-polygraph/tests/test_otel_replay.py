"""OTel span building/export and local cassettes + replay."""

from __future__ import annotations

import json

import pytest
from samples import entry, lost, run

from polygraph.otel import SpanSink, build_spans, new_span_id, new_trace_id
from polygraph.replay import cassette_path, read_cassette, replay, write_cassette
from polygraph.results import FaultName, Outcome


def _run_and_records():
    recs = [
        entry(0, "pay_invoice", fault=FaultName.LOST_RESPONSE, told_error=True),
        entry(1, "pay_invoice"),
    ]
    r = run("run_otel_000001", Outcome.DOUBLE_ACTION, lost(), recs)
    r.tokens = None
    return r, recs


def test_build_spans_follow_genai_conventions():
    r, recs = _run_and_records()
    spans = build_spans(r, recs, trace_id="c" * 32, span_id="e" * 16, include_content=True)
    root, first, second = spans
    assert root["attributes"]["gen_ai.operation.name"] == "invoke_agent"
    assert root["attributes"]["polygraph.outcome"] == "double_action"
    assert root["attributes"]["polygraph.fault.name"] == "lost_response"
    assert root["status"] == "ERROR" and root["parent_span_id"] is None
    assert first["name"] == "execute_tool pay_invoice"
    assert first["attributes"]["gen_ai.tool.name"] == "pay_invoice"
    assert first["attributes"]["mcp.method.name"] == "tools/call"
    assert first["attributes"]["mcp.protocol.version"] == "2026-07-28"
    assert first["attributes"]["polygraph.fault.name"] == "lost_response"
    assert first["attributes"]["error.type"] == "tool_error" and first["status"] == "ERROR"
    assert "INV-42" in first["attributes"]["gen_ai.tool.call.arguments"]
    assert first["span_id"] == recs[0].span_id and first["parent_span_id"] == "e" * 16
    assert second["status"] == "OK"
    assert root["start_ns"] <= first["start_ns"] < first["end_ns"]


def test_content_off_drops_payload_text():
    r, recs = _run_and_records()
    spans = build_spans(r, recs, trace_id="c" * 32, span_id="e" * 16, include_content=False)
    blob = json.dumps(spans)
    for text in ("Gateway timeout", "payment_id", "Done!", '"arguments"'):
        assert text not in blob


def test_ids():
    assert len(new_trace_id()) == 32 and len(new_span_id()) == 16


def test_span_sink_writes_local_json(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    r, recs = _run_and_records()
    sink = SpanSink(tmp_path / "spans", include_content=False)
    assert sink.export(r, recs, trace_id="c" * 32, span_id="e" * 16) == "json"
    data = json.loads((tmp_path / "spans" / f"{r.id}.json").read_text(encoding="utf-8"))
    assert len(data["spans"]) == 3
    sink.shutdown()


def test_span_sink_exports_otlp_with_matching_ids(tmp_path, monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda *a, **k: memory,
    )
    r, recs = _run_and_records()
    sink = SpanSink(tmp_path, include_content=True)
    assert sink.mode == "otlp"
    sink.export(r, recs, trace_id="c" * 32, span_id="e" * 16)
    sink.shutdown()
    spans = memory.get_finished_spans()
    assert len(spans) == 3
    by_name = {s.name: s for s in spans}
    root = next(s for s in spans if s.name.startswith("invoke_agent"))
    assert format(root.context.trace_id, "032x") == "c" * 32
    assert format(root.context.span_id, "016x") == "e" * 16
    tools = [s for s in spans if s.name == "execute_tool pay_invoice"]
    assert {format(s.context.span_id, "016x") for s in tools} == {recs[0].span_id, recs[1].span_id}
    assert all(s.parent is not None and s.parent.span_id == root.context.span_id for s in tools)
    assert by_name[root.name].attributes["polygraph.outcome"] == "double_action"


def test_cassette_round_trip(tmp_path):
    _, recs = _run_and_records()
    path = cassette_path(tmp_path, "run_abc12345")
    write_cassette(path, {"run_id": "run_abc12345", "test_id": "t::x"}, recs)
    header, back = read_cassette(path)
    assert header["cassette"] == 1 and header["test_id"] == "t::x"
    assert [b.entry.seq for b in back] == [0, 1]
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"cassette": 99}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_cassette(bad)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        read_cassette(empty)


def test_replay_tallies_outcomes(tmp_path):
    from samples import sample_results

    _, recs = _run_and_records()
    write_cassette(
        cassette_path(tmp_path, "run_abc12345"),
        {
            "run_id": "run_abc12345",
            "test_id": "t.py::test_x[lost]",
            "rootdir": str(tmp_path),
            "fault": lost().model_dump(mode="json"),
            "seed": 5,
            "outcome": "double_action",
        },
        recs,
    )
    calls = []

    def runner(args, cwd):
        calls.append(args)
        out = next(a.split("=", 1)[1] for a in args if a.startswith("--polygraph-json="))
        bad = len(calls) != 2
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(sample_results(bad=bad).to_json())
        return 0

    lines: list[str] = []
    rc = replay("run_abc12345", root=tmp_path, times=3, out=lines.append, runner=runner)
    assert rc == 0 and len(calls) == 3
    assert calls[0][0] == "t.py::test_x[lost]"
    assert any(a.startswith("--polygraph-replay=") for a in calls[0])
    text = "\n".join(lines)
    assert "Outcome double_action came back 2/3 times" in text
    assert "recovered 1" in text


def test_replay_not_run_when_no_results(tmp_path):
    write_cassette(
        cassette_path(tmp_path, "run_none0001"),
        {"run_id": "run_none0001", "test_id": "t.py::x", "fault": None, "outcome": "recovered"},
        [],
    )
    lines: list[str] = []
    replay("run_none0001", root=tmp_path, out=lines.append, runner=lambda a, c: 1)
    assert any("not run" in x for x in lines)
