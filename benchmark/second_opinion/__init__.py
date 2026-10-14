"""Optional second opinion: agent-polygraph's transcript-only ``verify()`` on every run.

agent-polygraph (https://github.com/najemwehbe/agent-polygraph, MIT) reads a
finished agent turn (tool calls, tool results, final message) and flags a
completion claim that the turn's own evidence contradicts. Polygraph checks the
real server state instead. Running both shows where a transcript-only check
agrees or disagrees with server truth.

    pip install -e ".[second-opinion]"
    python -m second_opinion out/<run>/results/*.json      # fills Run.second_opinion in place

If agent-polygraph is not installed, everything here is a no-op that says so.

How the transcript is built: from each run's ledger (what the agent asked and
what it was told), in order, plus its final answer. So it needs results files
written with payload text (``run.py`` does this by default). The comparison is
with Polygraph's ``silent_lie`` outcome, the case both tools look for.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polygraph.protocol import tool_text
from polygraph.results import Outcome, ResultsFile, Run, SecondOpinion, parse

PACKAGE = "agent_polygraph"


def available() -> bool:
    return importlib.util.find_spec(PACKAGE) is not None


def tool_label() -> str:
    try:
        return f"agent-polygraph {importlib.metadata.version('agent-polygraph')}"
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "agent-polygraph"


@dataclass
class Stats:
    runs: int = 0
    checked: int = 0
    skipped_no_text: int = 0
    flagged: int = 0
    agreed: int = 0
    reason: str | None = None


def _json(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def trajectory(run: Run) -> list[Any] | None:
    """agent-polygraph events for one run, or ``None`` if the ledger has no text."""
    from agent_polygraph import ToolCall, ToolResult

    events: list[Any] = []
    for entry in run.ledger:
        if entry.method != "tools/call":
            continue
        asked = _json(entry.asked.text)
        if asked is None and entry.asked.text is None:
            return None
        call_id = f"c{entry.seq}"
        args = asked.get("arguments", {}) if isinstance(asked, dict) else {}
        events.append(ToolCall(call_id, entry.tool or "", args))
        if entry.told is None:
            continue  # never answered (timeout): a dangling call, as agent-polygraph expects
        told = _json(entry.told.text)
        if isinstance(told, dict) and set(told) == {"error"}:
            content = str((told["error"] or {}).get("message", "error"))
        elif isinstance(told, dict):
            content = tool_text(told) or json.dumps(told.get("structuredContent", told))
        else:
            content = entry.told.text or ""
        events.append(ToolResult(call_id, content=content, is_error=entry.told_is_error))
    return events


def opinion(run: Run) -> SecondOpinion | None:
    from agent_polygraph import verify

    if run.final_answer is None or run.final_answer.text is None:
        return None
    events = trajectory(run)
    if events is None:
        return None
    v = verify(events, final_claim=run.final_answer.text, task=run.task or "")
    polygraph_says_lie = run.outcome is Outcome.SILENT_LIE
    checks = sorted({f.check for f in v.evidence})
    detail = ", ".join(x for x in [v.category or "", *checks] if x) or None
    return SecondOpinion(
        tool=tool_label(),
        flagged_lie=v.is_lie,
        agrees_with_polygraph=v.is_lie == polygraph_says_lie,
        detail=detail[:200] if detail else None,
    )


def annotate(results: ResultsFile) -> tuple[ResultsFile, Stats]:
    """A copy of ``results`` with ``second_opinion`` filled where possible."""
    stats = Stats(runs=len(results.runs))
    if not available():
        stats.reason = 'agent-polygraph is not installed (pip install -e ".[second-opinion]")'
        return results, stats
    out = results.model_copy(deep=True)
    for run in out.runs:
        op = opinion(run)
        if op is None:
            stats.skipped_no_text += 1
            continue
        run.second_opinion = op
        stats.checked += 1
        stats.flagged += int(op.flagged_lie)
        stats.agreed += int(op.agrees_with_polygraph)
    return out, stats


def annotate_file(path: Path) -> Stats:
    results = parse(path.read_text(encoding="utf-8"))
    updated, stats = annotate(results)
    if stats.checked:
        path.write_text(updated.to_json(indent=2), encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m second_opinion", description=__doc__.split("\n")[0])
    p.add_argument("results", nargs="+")
    ns = p.parse_args(argv)
    if not available():
        print(
            'agent-polygraph is not installed; nothing to do (pip install -e ".[second-opinion]")'
        )
        return 0
    for path in ns.results:
        s = annotate_file(Path(path))
        print(
            f"{path}: checked {s.checked}/{s.runs} runs, agent-polygraph flagged {s.flagged}, "
            f"agreed with Polygraph on {s.agreed}; {s.skipped_no_text} without text"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
