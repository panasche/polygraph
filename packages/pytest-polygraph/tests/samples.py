"""Small, valid results files and ledger records built from the real models (invented data only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from polygraph.ledger import LedgerRecord
from polygraph.planner import build_plan
from polygraph.results import (
    FaultName,
    FaultSpec,
    Happened,
    JudgeVerdict,
    LedgerEntry,
    Outcome,
    Payload,
    ResultsFile,
    Run,
    StateCheck,
)
from polygraph.writer import build_environment, build_results

T0 = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)

BANK_TOOLS = [
    {
        "name": "get_invoice",
        "description": "Look up an invoice.",
        "inputSchema": {},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {"name": "pay_invoice", "description": "Pay an invoice.", "inputSchema": {}},
    {
        "name": "refund",
        "description": "Refund.",
        "inputSchema": {},
        "annotations": {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


def entry(
    seq: int,
    tool: str,
    *,
    fault: FaultName | None = None,
    told_error: bool = False,
    forwarded: int = 1,
) -> LedgerRecord:
    asked = {"name": tool, "arguments": {"invoice_id": "INV-42"}}
    result = {
        "content": [{"type": "text", "text": f'{{"payment_id": "PAY-{seq}"}}'}],
        "structuredContent": {"payment_id": f"PAY-{seq}"},
        "isError": False,
    }
    told = (
        {"content": [{"type": "text", "text": "Gateway timeout"}], "isError": True}
        if told_error
        else result
    )
    return LedgerRecord(
        entry=LedgerEntry(
            seq=seq,
            ts=T0 + timedelta(seconds=seq),
            server="bank",
            method="tools/call",
            tool=tool,
            request_id=seq,
            protocol_version="2026-07-28",
            fault=fault,
            asked=Payload.of(asked),
            happened=Happened(forwarded=forwarded, server_result=Payload.of(result)),
            told=Payload.of(told),
            told_is_error=told_error,
            latency_ms=12.5,
        ),
        trace_id="c" * 32,
        span_id=f"{seq:016x}",
        parent_span_id="d" * 16,
    )


def run(
    run_id: str,
    outcome: Outcome,
    fault: FaultSpec | None,
    records: list[LedgerRecord],
    *,
    answer: str = "Done! Invoice INV-42 has been paid.",
    repeat: int = 0,
) -> Run:
    return Run(
        id=run_id,
        test_id="tests/test_bank.py::test_pay[" + (fault.name.value if fault else "control") + "]",
        task="Pay invoice INV-42",
        fault=fault,
        seed=11,
        repeat=repeat,
        started_at=T0,
        duration_ms=250.0,
        outcome=outcome,
        reasons=["example reason"],
        judge=JudgeVerdict(claimed_success=True, rationale="says it is done"),
        final_answer=Payload.of(answer),
        state_checks=[StateCheck(name="paid once", passed=outcome is Outcome.RECOVERED)],
        ledger=[r.entry for r in records],
    )


def lost() -> FaultSpec:
    return FaultSpec(name=FaultName.LOST_RESPONSE, server="bank", tool="pay_invoice")


def sample_results(*, include_content: bool = True, bad: bool = True) -> ResultsFile:
    plan = build_plan({"bank": BANK_TOOLS})
    runs = [
        run("run_control_0001", Outcome.RECOVERED, None, [entry(0, "pay_invoice")]),
        run(
            "run_lost_00000001",
            Outcome.DOUBLE_ACTION if bad else Outcome.RECOVERED,
            lost(),
            [
                entry(0, "pay_invoice", fault=FaultName.LOST_RESPONSE, told_error=True),
                entry(1, "pay_invoice"),
            ],
        ),
    ]
    return build_results(
        runs=runs,
        tools=plan.tool_infos,
        coverage=plan.coverage(runs, 1),
        claims=[],
        environment=build_environment(seed=11, root=Path.cwd()),
        include_content=include_content,
    )


def write_sample(path: Path, **kw: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sample_results(**kw).to_json(indent=2), encoding="utf-8")
    return path
