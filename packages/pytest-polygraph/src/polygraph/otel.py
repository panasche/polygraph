"""OpenTelemetry spans for each run, following the OTel GenAI semantic conventions.

- One ``invoke_agent`` span per run, with the fault and outcome as attributes.
- One child span per ledger entry: ``execute_tool <name>`` for ``tools/call``
  (``gen_ai.operation.name = execute_tool``), ``<method>`` for the rest, with
  the MCP method and protocol version. The span ids match the ``traceparent``
  the proxy put in each request's ``_meta``, so server-side spans join the trace.

If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set and the ``otel`` extra is installed
(``pip install pytest-polygraph[otel]``), spans go to that endpoint (protocol
from ``OTEL_EXPORTER_OTLP_PROTOCOL``: ``http/protobuf`` by default, or
``grpc``; headers from ``OTEL_EXPORTER_OTLP_HEADERS``). Otherwise they are
written as local JSON to ``.polygraph/spans/<run_id>.json``.

``--polygraph-otel-content=off`` drops payload text (tool arguments, results
and the final answer) from every span.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .ledger import LedgerRecord
from .results import Run

AttrValue = str | bool | int | float | list[str]


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def build_spans(
    run: Run,
    records: Sequence[LedgerRecord],
    *,
    trace_id: str,
    span_id: str,
    include_content: bool,
) -> list[dict[str, Any]]:
    """Plain-dict spans (``name``, ids, ``start_ns``, ``end_ns``, ``attributes``, ``status``)."""
    start = run.started_at
    end = start + timedelta(milliseconds=run.duration_ms)
    attrs: dict[str, AttrValue] = {
        "gen_ai.operation.name": "invoke_agent",
        "polygraph.run.id": run.id,
        "polygraph.test.id": run.test_id,
        "polygraph.seed": run.seed,
        "polygraph.repeat": run.repeat,
        "polygraph.outcome": run.outcome.value,
        "polygraph.reasons": list(run.reasons),
    }
    if run.task:
        attrs["polygraph.task"] = run.task
    if run.fault is not None:
        attrs.update(
            {
                "polygraph.fault.name": run.fault.name.value,
                "polygraph.fault.family": run.fault.family.value,
                "polygraph.fault.server": run.fault.server,
                "polygraph.fault.tool": run.fault.tool,
                "polygraph.fault.on_call": run.fault.on_call,
            }
        )
    if run.tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = run.tokens.input
        attrs["gen_ai.usage.output_tokens"] = run.tokens.output
    if run.protocol_version:
        attrs["mcp.protocol.version"] = run.protocol_version
    if include_content and run.final_answer is not None and run.final_answer.text is not None:
        attrs["polygraph.final_answer"] = run.final_answer.text
    if run.judge is not None and run.judge.claimed_success is not None:
        attrs["polygraph.judge.claimed_success"] = run.judge.claimed_success
    bad = run.outcome.value in {"silent_lie", "double_action", "hijacked", "loop", "crash"}
    spans = [
        {
            "name": f"invoke_agent {run.test_id.split('::')[-1]}",
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": None,
            "start_ns": _ns(start),
            "end_ns": _ns(end),
            "attributes": attrs,
            "status": "ERROR" if bad else "OK",
        }
    ]
    for rec in records:
        e = rec.entry
        s = e.ts
        f = s + timedelta(milliseconds=e.latency_ms)
        a: dict[str, AttrValue] = {
            "mcp.method.name": e.method,
            "polygraph.server": e.server,
            "polygraph.forwarded": e.happened.forwarded,
            "polygraph.told_is_error": e.told_is_error,
        }
        if e.protocol_version:
            a["mcp.protocol.version"] = e.protocol_version
        if e.request_id is not None:
            a["jsonrpc.request.id"] = str(e.request_id)
        name = e.method
        if e.method == "tools/call" and e.tool:
            name = f"execute_tool {e.tool}"
            a["gen_ai.operation.name"] = "execute_tool"
            a["gen_ai.tool.name"] = e.tool
            if e.request_id is not None:
                a["gen_ai.tool.call.id"] = str(e.request_id)
        if e.fault is not None:
            a["polygraph.fault.name"] = e.fault.value
        if e.happened.note:
            a["polygraph.note"] = e.happened.note
        if e.told_is_error:
            a["error.type"] = "tool_error" if e.told is not None else "no_response"
        if include_content:
            if e.asked.text is not None:
                a["gen_ai.tool.call.arguments"] = e.asked.text
            if e.told is not None and e.told.text is not None:
                a["gen_ai.tool.call.result"] = e.told.text
        spans.append(
            {
                "name": name,
                "trace_id": rec.trace_id or trace_id,
                "span_id": rec.span_id or new_span_id(),
                "parent_span_id": span_id
                if (rec.trace_id in (None, trace_id))
                else rec.parent_span_id,
                "start_ns": _ns(s),
                "end_ns": _ns(f),
                "attributes": a,
                "status": "ERROR" if e.told_is_error else "OK",
            }
        )
    return spans


class SpanSink:
    """Sends spans over OTLP when configured, else writes local JSON. One per test session."""

    def __init__(self, out_dir: Path, *, include_content: bool) -> None:
        self.out_dir = out_dir
        self.include_content = include_content
        self.mode = "json"
        self._provider: Any = None
        self._tracer: Any = None
        self._ids: Any = None
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        ):
            try:
                self._setup_otlp()
                self.mode = "otlp"
            except ImportError:
                self.mode = "json"

    def _setup_otlp(self) -> None:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.id_generator import IdGenerator

        from . import __version__

        class _Queued(IdGenerator):
            def __init__(self) -> None:
                self.trace: int | None = None
                self.span: int | None = None

            def generate_span_id(self) -> int:
                v, self.span = self.span, None
                return v if v is not None else secrets.randbits(64) or 1

            def generate_trace_id(self) -> int:
                v, self.trace = self.trace, None
                return v if v is not None else secrets.randbits(128) or 1

        protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[assignment]
                OTLPSpanExporter,
            )
        self._ids = _Queued()
        resource = Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", "polygraph"),
                "service.version": __version__,
            }
        )
        self._provider = TracerProvider(resource=resource, id_generator=self._ids)
        self._provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        self._tracer = self._provider.get_tracer("polygraph", __version__)

    def export(
        self, run: Run, records: Sequence[LedgerRecord], *, trace_id: str, span_id: str
    ) -> str:
        spans = build_spans(
            run, records, trace_id=trace_id, span_id=span_id, include_content=self.include_content
        )
        if self.mode == "otlp":
            self._export_otlp(spans)
        else:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{run.id}.json"
            path.write_text(json.dumps({"spans": spans}, indent=2), encoding="utf-8")
        return self.mode

    def _export_otlp(self, spans: list[dict[str, Any]]) -> None:
        from opentelemetry import trace
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            Status,
            StatusCode,
            TraceFlags,
        )

        root = spans[0]
        self._ids.trace = int(root["trace_id"], 16)
        self._ids.span = int(root["span_id"], 16)
        root_span = self._tracer.start_span(
            root["name"], start_time=root["start_ns"], attributes=root["attributes"]
        )
        parent_ctx = trace.set_span_in_context(root_span)
        for s in spans[1:]:
            ctx = parent_ctx
            if s["trace_id"] != root["trace_id"] and s.get("parent_span_id"):
                ctx = trace.set_span_in_context(
                    NonRecordingSpan(
                        SpanContext(
                            int(s["trace_id"], 16),
                            int(s["parent_span_id"], 16),
                            True,
                            TraceFlags(TraceFlags.SAMPLED),
                        )
                    )
                )
                self._ids.trace = int(s["trace_id"], 16)
            self._ids.span = int(s["span_id"], 16)
            child = self._tracer.start_span(
                s["name"], context=ctx, start_time=s["start_ns"], attributes=s["attributes"]
            )
            if s["status"] == "ERROR":
                child.set_status(Status(StatusCode.ERROR))
            child.end(end_time=s["end_ns"])
        if root["status"] == "ERROR":
            root_span.set_status(Status(StatusCode.ERROR))
        root_span.end(end_time=root["end_ns"])

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()
