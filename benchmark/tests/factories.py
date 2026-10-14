"""Build small, valid results files in memory (invented data only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polygraph.metrics import summarize
from polygraph.results import (
    Coverage,
    Environment,
    FaultName,
    FaultSpec,
    Happened,
    JudgeVerdict,
    LedgerEntry,
    Outcome,
    Payload,
    ResultsFile,
    Run,
    TokenUsage,
)

T0 = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


def entry(
    seq: int,
    tool: str,
    args: dict[str, Any],
    told: Any,
    *,
    is_error: bool = False,
    forwarded: int = 1,
) -> LedgerEntry:
    """One tools/call ledger line; ``told=None`` means the agent never got an answer."""
    if told is None:
        told_payload = None
    elif isinstance(told, str):
        told_payload = Payload.of(
            {"content": [{"type": "text", "text": told}], "isError": is_error}
        )
    else:
        told_payload = Payload.of(told)
    return LedgerEntry(
        seq=seq,
        ts=T0,
        server="bank",
        method="tools/call",
        tool=tool,
        protocol_version="2026-07-28",
        asked=Payload.of({"name": tool, "arguments": args}),
        happened=Happened(forwarded=forwarded),
        told=told_payload,
        told_is_error=is_error,
    )


def run(
    rid: str,
    *,
    outcome: Outcome = Outcome.RECOVERED,
    answer: str | None = "Done.",
    fault: str | None = None,
    tool: str = "pay_invoice",
    ledger: list[LedgerEntry] | None = None,
    tokens: tuple[int, int] | None = (100, 20),
    claimed: bool | None = True,
    task: str = "Please pay invoice INV-42 for Customer A.",
) -> Run:
    return Run(
        id=rid,
        test_id=f"tasks/test_bank.py::test_x[{fault or 'control'}]",
        task=task,
        fault=FaultSpec(name=FaultName(fault), server="bank", tool=tool) if fault else None,
        seed=1,
        started_at=T0,
        duration_ms=5.0,
        outcome=outcome,
        judge=JudgeVerdict(claimed_success=claimed, method="llm", model="openai:fake"),
        final_answer=Payload.of(answer) if answer is not None else None,
        ledger=ledger or [],
        tokens=TokenUsage(input=tokens[0], output=tokens[1]) if tokens else None,
    )


def results(
    runs: list[Run],
    *,
    framework: str = "langgraph",
    model: str = "openai:fake-model",
    config_label: str = "default",
) -> ResultsFile:
    cov = Coverage(applicable=0, covered=0)
    content = any(
        p is not None and p.text is not None
        for r in runs
        for p in [r.final_answer, *[e.asked for e in r.ledger], *[e.told for e in r.ledger]]
    )
    return ResultsFile(
        generated_at=T0,
        content_included=content,
        environment=Environment(
            polygraph_version="0.1.0",
            python_version="3.12.0",
            platform="test",
            framework=framework,
            framework_version="1.0.0",
            config_label=config_label,
            model=model,
            protocol_versions=["2026-07-28"],
            seed=1,
        ),
        coverage=cov,
        runs=runs,
        summary=summarize(runs, cov),
    )


def write(path: Path, rf: ResultsFile) -> Path:
    path.write_text(rf.to_json(indent=2), encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))
    return path
