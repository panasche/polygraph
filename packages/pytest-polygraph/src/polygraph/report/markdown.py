"""The sticky pull-request comment.

Works from a local results file (optionally against a base results file) or
from the cloud ``Compare`` response (see docs/CONTRACTS.md section 4). The
comment never contains payload text: only rates, counts, tool and fault names.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..metrics import wilson
from ..results import BAD_OUTCOMES, Priority, Rate, ResultsFile

MARKER = "<!-- polygraph -->"

#: Rates where lower is better, in headline order.
_BAD_METRICS: tuple[tuple[str, str], ...] = (
    ("silent_lie", "silent-lie"),
    ("double_action", "double-action"),
    ("hijack", "hijack"),
)
_TABLE_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("silent_lie", "Silent lie", False),
    ("double_action", "Double action", False),
    ("hijack", "Hijacked", False),
    ("success", "Success", True),
)
_MAX_PAIRS = 8
_MAX_UNTESTED = 5


@dataclass
class _Side:
    rates: dict[str, Rate | None] = field(default_factory=dict)
    applicable: int | None = None
    covered: int | None = None


@dataclass
class _PairDelta:
    key: str
    base_runs: int
    base_bad: int
    head_runs: int
    head_bad: int


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #


def _pct_value(value: float | None) -> str:
    if value is None:
        return "n/a"
    pct = value * 100
    if 0 < pct < 1:
        return "<1%"
    if 99 < pct < 100:
        return ">99%"
    return f"{int(pct + 0.5)}%"  # round half up: 12.5% shows as 13%


def _pct(rate: Rate | None) -> str:
    return "n/a" if rate is None else _pct_value(rate.value)


def _code(text: str) -> str:
    """Inline code span that cannot be broken out of."""
    return "`" + text.replace("`", "'").replace("\n", " ") + "`"


def _plain(text: str, limit: int = 200) -> str:
    """Make free text safe for one markdown line: no HTML, no mentions, no newlines."""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    text = html.escape(text, quote=False)
    return text.replace("@", "@​").replace("|", "\\|")


def _to_rate(obj: Any) -> Rate | None:
    if obj is None:
        return None
    if isinstance(obj, Rate):
        return obj
    if isinstance(obj, Mapping):
        num = int(obj.get("numerator", 0) or 0)
        den = int(obj.get("denominator", 0) or 0)
        if obj.get("value") is None:
            return wilson(num, den)
        return Rate(
            numerator=num,
            denominator=den,
            value=obj.get("value"),
            ci_low=obj.get("ci_low"),
            ci_high=obj.get("ci_high"),
        )
    return None


def _direction(base: Rate | None, head: Rate | None) -> int | None:
    """-1 when the rate went down, 1 when up, 0 when equal, None when unknown."""
    if base is None or head is None or base.value is None or head.value is None:
        return None
    if _pct(base) == _pct(head):
        return 0
    return -1 if head.value < base.value else 1


def _bad_mark(base: Rate | None, head: Rate | None) -> str:
    """✅ when a bad rate went down or stays at zero, ❌ when it went up."""
    d = _direction(base, head)
    if d is None:
        return ""
    if d < 0:
        return " ✅"
    if d > 0:
        return " ❌"
    return " ✅" if head is not None and head.numerator == 0 else ""


def _good_mark(base: Rate | None, head: Rate | None) -> str:
    d = _direction(base, head)
    if d is None or d == 0:
        return ""
    return " ✅" if d > 0 else " ❌"


def _coverage_text(side: _Side) -> str:
    if side.applicable is None or side.covered is None:
        return "n/a"
    return f"{side.covered}/{side.applicable}"


def _coverage_arrow(base: _Side, head: _Side) -> str:
    if None in (base.applicable, base.covered, head.applicable, head.covered):
        return ""
    assert base.applicable is not None and base.covered is not None
    assert head.applicable is not None and head.covered is not None
    if base.applicable and head.applicable:
        before = base.covered / base.applicable
        after = head.covered / head.applicable
    else:
        before, after = float(base.covered), float(head.covered)
    if after > before:
        return " ⬆️"
    if after < before:
        return " ⬇️"
    return ""


def _points(base: Rate | None, head: Rate | None) -> str:
    if base is None or head is None or base.value is None or head.value is None:
        return "n/a"
    diff = round((head.value - base.value) * 100)
    if diff == 0:
        return "no change"
    return f"{diff:+d} pts"


# --------------------------------------------------------------------------- #
# Collecting the numbers
# --------------------------------------------------------------------------- #


def _side_from_results(results: ResultsFile) -> _Side:
    s = results.summary
    return _Side(
        rates={
            "silent_lie": s.silent_lie,
            "double_action": s.double_action,
            "hijack": s.hijack,
            "success": s.success,
        },
        applicable=results.coverage.applicable,
        covered=results.coverage.covered,
    )


def _side_from_report(report: Mapping[str, Any] | None) -> _Side:
    report = report or {}
    cov = report.get("coverage") or {}
    return _Side(
        rates={k: _to_rate(report.get(k)) for k, _, _ in _TABLE_METRICS},
        applicable=cov.get("applicable"),
        covered=cov.get("covered"),
    )


def _sides_from_compare(compare: Mapping[str, Any]) -> tuple[_Side, _Side]:
    base = _side_from_report(compare.get("base"))
    head = _side_from_report(compare.get("head"))
    for key, metric in (compare.get("metrics") or {}).items():
        if not isinstance(metric, Mapping):
            continue
        if metric.get("base") is not None:
            base.rates[key] = _to_rate(metric["base"])
        if metric.get("head") is not None:
            head.rates[key] = _to_rate(metric["head"])
    cov = compare.get("coverage") or {}
    for side, raw in ((base, cov.get("base")), (head, cov.get("head"))):
        if isinstance(raw, Mapping):
            side.applicable = raw.get("applicable", side.applicable)
            side.covered = raw.get("covered", side.covered)
    return base, head


def _pair_counts(results: ResultsFile) -> dict[str, tuple[int, int]]:
    """``"server/tool × fault" -> (runs, bad runs)`` over faulted runs."""
    runs: Counter[str] = Counter()
    bad: Counter[str] = Counter()
    for run in results.runs:
        f = run.fault
        if f is None:
            continue
        key = f"{f.server}/{f.tool} × {f.name.value}"
        runs[key] += 1
        if run.outcome in BAD_OUTCOMES:
            bad[key] += 1
    return {k: (n, bad[k]) for k, n in runs.items()}


def _pairs_from_compare(compare: Mapping[str, Any]) -> list[_PairDelta]:
    out: list[_PairDelta] = []
    for p in compare.get("pairs") or []:
        if not isinstance(p, Mapping):
            continue
        b = p.get("base") or {}
        h = p.get("head") or {}
        key = f"{p.get('server', '?')}/{p.get('tool', '?')} × {p.get('fault', '?')}"
        out.append(
            _PairDelta(
                key,
                int(b.get("runs", 0) or 0),
                int(b.get("bad", 0) or 0),
                int(h.get("runs", 0) or 0),
                int(h.get("bad", 0) or 0),
            )
        )
    return out


def _pairs_from_results(base: ResultsFile, head: ResultsFile) -> list[_PairDelta]:
    b = _pair_counts(base)
    h = _pair_counts(head)
    out: list[_PairDelta] = []
    for key in sorted(set(b) | set(h)):
        br, bb = b.get(key, (0, 0))
        hr, hb = h.get(key, (0, 0))
        out.append(_PairDelta(key, br, bb, hr, hb))
    return out


def _split_pairs(pairs: list[_PairDelta]) -> tuple[list[_PairDelta], list[_PairDelta]]:
    worse: list[_PairDelta] = []
    better: list[_PairDelta] = []
    for p in pairs:
        if p.head_runs == 0:
            continue  # not run on this side: nothing to say
        head_share = p.head_bad / p.head_runs
        if p.base_runs == 0:
            if p.head_bad:
                worse.append(p)
            continue
        base_share = p.base_bad / p.base_runs
        if head_share > base_share:
            worse.append(p)
        elif head_share < base_share:
            better.append(p)
    worse.sort(key=lambda p: -(p.head_bad / p.head_runs))
    better.sort(key=lambda p: -(p.base_bad / p.base_runs))
    return worse, better


def _pair_line(p: _PairDelta) -> str:
    before = f"{p.base_bad}/{p.base_runs} bad" if p.base_runs else "not run"
    return f"- {_code(p.key)}: {before} → {p.head_bad}/{p.head_runs} bad"


def _capped(lines: list[str], limit: int) -> list[str]:
    if len(lines) <= limit:
        return lines
    return [*lines[:limit], f"- and {len(lines) - limit} more"]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def render_pr_comment(
    results: ResultsFile | None = None,
    *,
    base: ResultsFile | None = None,
    compare: dict[str, Any] | None = None,
    base_label: str = "main",
    details_url: str | None = None,
) -> str:
    """Render the PR comment as GitHub-flavoured markdown.

    Pass ``results`` (this run), optionally with ``base`` (the base branch's
    results file); or pass a cloud ``compare`` dict, in which case ``results``
    is optional and only adds the untested pairs and failed server claims.
    """
    if results is None and compare is None:
        raise ValueError("render_pr_comment needs results or a compare dict")

    base_side: _Side | None
    pairs: list[_PairDelta] | None = None
    if compare is not None:
        base_side, head_side = _sides_from_compare(compare)
        branch = (compare.get("base") or {}).get("branch")
        if branch:
            base_label = str(branch)
        pairs = _pairs_from_compare(compare)
    else:
        assert results is not None
        head_side = _side_from_results(results)
        base_side = _side_from_results(base) if base is not None else None
        if base is not None:
            pairs = _pairs_from_results(base, results)

    lines: list[str] = [MARKER]
    lines.append(_headline(base_side, head_side, base_label))
    lines.append("")
    lines.extend(_rates_table(base_side, head_side, base_label))

    if pairs is not None:
        worse, better = _split_pairs(pairs)
        if worse:
            lines += ["", "**Got worse**", *_capped([_pair_line(p) for p in worse], _MAX_PAIRS)]
        if better:
            lines += ["", "**Got better**", *_capped([_pair_line(p) for p in better], _MAX_PAIRS)]
    elif results is not None:
        counts = _pair_counts(results)
        bad_pairs = sorted(
            ((k, n, b) for k, (n, b) in counts.items() if b),
            key=lambda item: (-item[2] / item[1], item[0]),
        )
        if bad_pairs:
            rows = [f"- {_code(k)}: {b}/{n} bad" for k, n, b in bad_pairs]
            lines += ["", "**Where it went wrong**", *_capped(rows, _MAX_PAIRS)]

    if results is not None:
        lines.extend(_untested(results))
        lines.extend(_failed_claims(results))

    if details_url:
        safe_url = details_url.strip().replace(" ", "%20").replace(")", "%29")
        lines += ["", f"[See the full report]({safe_url})"]
    return "\n".join(lines) + "\n"


def _headline(base: _Side | None, head: _Side, base_label: str) -> str:
    parts: list[str] = []
    if base is None:
        for key, label in _BAD_METRICS:
            rate = head.rates.get(key)
            if rate is None or rate.denominator == 0:
                parts.append(f"{label} n/a")
            else:
                parts.append(f"{label} {rate.numerator}/{rate.denominator} ({_pct(rate)})")
        parts.append(f"coverage {_coverage_text(head)}")
        return "**Polygraph**: " + " · ".join(parts)

    for key, label in _BAD_METRICS:
        b, h = base.rates.get(key), head.rates.get(key)
        parts.append(f"{label} {_pct(b)} → {_pct(h)}{_bad_mark(b, h)}")
    arrow = _coverage_arrow(base, head)
    parts.append(f"coverage {_coverage_text(base)} → {_coverage_text(head)}{arrow}")
    return f"**Polygraph** vs {_code(base_label)}: " + " · ".join(parts)


def _cell(rate: Rate | None) -> str:
    if rate is None or rate.denominator == 0:
        return "n/a"
    return f"{_pct(rate)} ({rate.numerator}/{rate.denominator})"


def _rates_table(base: _Side | None, head: _Side, base_label: str) -> list[str]:
    if base is None:
        rows = ["| Metric | This run | 95% range |", "|---|---|---|"]
        for key, label, _ in _TABLE_METRICS:
            rate = head.rates.get(key)
            if rate is not None and rate.ci_low is not None and rate.ci_high is not None:
                span = f"{_pct_value(rate.ci_low)} to {_pct_value(rate.ci_high)}"
            else:
                span = "n/a"
            rows.append(f"| {label} | {_cell(rate)} | {span} |")
        return rows

    rows = [f"| Metric | {_code(base_label)} | This PR | Change |", "|---|---|---|---|"]
    for key, label, higher_is_better in _TABLE_METRICS:
        b, h = base.rates.get(key), head.rates.get(key)
        mark = _good_mark(b, h) if higher_is_better else _bad_mark(b, h)
        rows.append(f"| {label} | {_cell(b)} | {_cell(h)} | {_points(b, h)}{mark} |")
    rows.append(
        f"| Coverage | {_coverage_text(base)} | {_coverage_text(head)} | "
        f"{_coverage_arrow(base, head).strip() or 'no change'} |"
    )
    return rows


def _untested(results: ResultsFile) -> list[str]:
    todo = [p for p in results.coverage.pairs if not p.covered][:_MAX_UNTESTED]
    if not todo:
        return []
    lines = ["", "**Riskiest untested pairs**"]
    for p in todo:
        top = " (top priority)" if p.priority is Priority.TOP else ""
        key = f"{p.server}/{p.tool} × {p.fault.value}"
        lines.append(f"- {_code(key)}{top}: {p.runs_done} of {p.runs_required} runs done")
    return lines


def _failed_claims(results: ResultsFile) -> list[str]:
    failed = [c for c in results.server_claims if not c.held]
    if not failed:
        return []
    lines = ["", "**Server claims that did not hold**"]
    for c in failed[:_MAX_PAIRS]:
        claim = "idempotent" if c.claim == "idempotent" else "read only"
        lines.append(f"- {_code(f'{c.server}/{c.tool}')} says {claim}: {_plain(c.evidence)}")
    if len(failed) > _MAX_PAIRS:
        lines.append(f"- and {len(failed) - _MAX_PAIRS} more")
    return lines
