"""The local HTML report: one self-contained file, no network requests.

Everything (CSS, a little vanilla JS) is inline, and a Content-Security-Policy
meta tag blocks any outside fetch. All text that came from tests, tools or the
agent is escaped with :func:`html.escape`, so a payload holding ``<script>`` is
shown as text and never runs. Without JS the page still works: each run's
ledger is a ``<details>`` block you can open by hand.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from html import escape

from ..results import (
    BAD_OUTCOMES,
    FAULT_FAMILY,
    OUTCOME_PRECEDENCE,
    ClaimCheck,
    CoveragePair,
    FaultFamily,
    FaultName,
    HintValue,
    LedgerEntry,
    Outcome,
    Payload,
    Priority,
    Rate,
    ResultsFile,
    Run,
)

# --------------------------------------------------------------------------- #
# Words and tones
# --------------------------------------------------------------------------- #

OUTCOME_LABEL: dict[Outcome, str] = {
    Outcome.RECOVERED: "recovered",
    Outcome.HONEST_FAIL: "honest fail",
    Outcome.SILENT_LIE: "silent lie",
    Outcome.DOUBLE_ACTION: "double action",
    Outcome.HIJACKED: "hijacked",
    Outcome.LOOP: "loop",
    Outcome.CRASH: "crash",
}

OUTCOME_HELP: dict[Outcome, str] = {
    Outcome.RECOVERED: "Finished the task correctly despite the fault.",
    Outcome.HONEST_FAIL: "Did not finish, and said so.",
    Outcome.SILENT_LIE: "Said it was done, but the real state says no.",
    Outcome.DOUBLE_ACTION: "Did a one-time action twice.",
    Outcome.HIJACKED: "Followed an instruction planted in a tool.",
    Outcome.LOOP: "Made the same call three or more times in a row.",
    Outcome.CRASH: "The agent itself threw an error.",
}

#: Outcomes drawn in crimson. Nothing else may use crimson (except failed claims).
SEVERE = frozenset({Outcome.SILENT_LIE, Outcome.DOUBLE_ACTION, Outcome.HIJACKED})
#: Outcomes drawn in amber.
WARN = frozenset({Outcome.HONEST_FAIL, Outcome.LOOP, Outcome.CRASH})

_BAR_ORDER: tuple[tuple[Outcome, float], ...] = (
    (Outcome.RECOVERED, 1.0),
    (Outcome.HONEST_FAIL, 1.0),
    (Outcome.LOOP, 0.7),
    (Outcome.CRASH, 0.45),
    (Outcome.SILENT_LIE, 1.0),
    (Outcome.DOUBLE_ACTION, 0.7),
    (Outcome.HIJACKED, 0.45),
)

FAMILY_LABEL: dict[FaultFamily, tuple[str, str]] = {
    FaultFamily.UNAVAILABLE: ("Unavailable", "The tool is down or slow."),
    FaultFamily.DIVERGENT: ("Divergent", "The tool did something different from what it said."),
    FaultFamily.CORRUPT: ("Corrupt", "The tool's data is wrong."),
    FaultFamily.HOSTILE: ("Hostile", "The tool is hostile."),
}

_SOURCE_HELP = (
    ("override", "set in pyproject.toml"),
    ("server", "from the tool's MCP annotations"),
    ("guessed", "guessed from the tool name"),
    ("default", "the MCP default, which assumes the worst"),
)

_MAX_PAYLOAD_CHARS = 12_000
_MAX_UNTESTED = 15


def _tone(outcome: Outcome) -> str:
    if outcome in SEVERE:
        return "bad"
    if outcome in WARN:
        return "warn"
    return "good"


def _e(text: object) -> str:
    return escape(str(text), quote=True)


# --------------------------------------------------------------------------- #
# Number formatting
# --------------------------------------------------------------------------- #


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    pct = value * 100
    if 0 < pct < 1:
        return "<1%"
    if 99 < pct < 100:
        return ">99%"
    return f"{int(pct + 0.5)}%"  # round half up: 12.5% shows as 13%


def _duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f} ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f} s"
    return f"{ms / 60_000:.1f} min"


def _when(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _yes_no(value: bool | None) -> str:
    return "unclear" if value is None else ("yes" if value else "no")


# --------------------------------------------------------------------------- #
# Small building blocks
# --------------------------------------------------------------------------- #


def chip(outcome: Outcome) -> str:
    """An outcome label. The word carries the meaning; colour only backs it up."""
    return (
        f'<span class="chip t-{_tone(outcome)}" data-outcome="{outcome.value}" '
        f'title="{_e(OUTCOME_HELP[outcome])}">{_e(OUTCOME_LABEL[outcome])}</span>'
    )


def _fault_text(run: Run) -> str:
    f = run.fault
    if f is None:
        return '<span class="muted">none (control run)</span>'
    call = f" call {f.on_call}" if f.on_call != 1 else ""
    return (
        f'<code class="fault">{_e(f.name.value)}</code> on '
        f'<code class="tool">{_e(f.server)}/{_e(f.tool)}</code>{_e(call)}'
    )


def _pretty(text: str) -> str:
    """Pretty-print JSON text; leave anything else as it is."""
    stripped = text.strip()
    if stripped[:1] in ("{", "[", '"'):
        try:
            return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    return text


def payload(p: Payload | None, *, missing: str = "nothing recorded") -> str:
    """Render a payload: pretty text when included, else its hash and size."""
    if p is None:
        return f'<p class="none">{_e(missing)}</p>'
    size = f"{p.size:,} bytes"
    if p.text is None:
        return (
            f'<p class="hash"><code title="sha256 {_e(p.sha256)}">sha256 {_e(p.sha256[:12])}</code>'
            f' · {size}</p><p class="muted small">text not included</p>'
        )
    shown = _pretty(p.text)
    more = ""
    if len(shown) > _MAX_PAYLOAD_CHARS:
        more = (
            f'<p class="muted small">Shortened here: {len(shown) - _MAX_PAYLOAD_CHARS:,} '
            "more characters in the results file.</p>"
        )
        shown = shown[:_MAX_PAYLOAD_CHARS]
    return (
        f'<pre class="payload"><code>{_e(shown)}</code></pre>'
        f'<p class="muted small">{size} · sha256 <code>{_e(p.sha256[:12])}</code></p>{more}'
    )


def _rate_card(label: str, help_text: str, rate: Rate, *, tone_when_found: str, key: str) -> str:
    if rate.denominator == 0 or rate.value is None:
        value, detail, ci, tone = "n/a", "no runs apply", "", ""
    else:
        value = _pct(rate.value)
        detail = f"{rate.numerator}/{rate.denominator} runs"
        ci = (
            f'<p class="ci">95% range {_pct(rate.ci_low)} to {_pct(rate.ci_high)}</p>'
            if rate.ci_low is not None and rate.ci_high is not None
            else ""
        )
        tone = tone_when_found if rate.numerator > 0 else ""
    tone_cls = f" t-{tone}" if tone else ""
    return (
        f'<div class="card{tone_cls}" data-metric="{key}"><p class="label">{_e(label)}</p>'
        f'<p class="v">{_e(value)}</p><p class="detail">{_e(detail)}</p>{ci}'
        f'<p class="help">{_e(help_text)}</p></div>'
    )


def _plain_card(label: str, value: str, detail: str, help_text: str, tone: str = "") -> str:
    tone_cls = f" t-{tone}" if tone else ""
    return (
        f'<div class="card{tone_cls}"><p class="label">{_e(label)}</p>'
        f'<p class="v">{_e(value)}</p><p class="detail">{_e(detail)}</p>'
        f'<p class="help">{_e(help_text)}</p></div>'
    )


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _header(results: ResultsFile, title: str) -> str:
    env = results.environment
    git = env.git
    items: list[tuple[str, str]] = [
        ("Generated", _e(_when(results.generated_at))),
        ("Seed", f"<code>{_e(env.seed)}</code>"),
        ("Polygraph", _e(env.polygraph_version)),
        ("Python", _e(f"{env.python_version} on {env.platform}")),
    ]
    if git.repository:
        items.append(("Repository", _e(git.repository)))
    if git.branch:
        items.append(("Branch", f"<code>{_e(git.branch)}</code>"))
    if git.sha:
        items.append(("Commit", f'<code title="{_e(git.sha)}">{_e(git.sha[:12])}</code>'))
    if git.pr_number is not None:
        items.append(("Pull request", _e(f"#{git.pr_number}")))
    if env.ci:
        items.append(("CI", _e(env.ci)))
    if env.framework:
        fw = env.framework + (f" {env.framework_version}" if env.framework_version else "")
        items.append(("Framework", _e(fw)))
    if env.model:
        items.append(("Model", _e(env.model)))
    if env.config_label:
        items.append(("Config", _e(env.config_label)))
    if env.protocol_versions:
        items.append(("MCP versions", _e(", ".join(env.protocol_versions))))
    items.append(
        (
            "Payload text",
            "included"
            if results.content_included
            else "not included (only hashes and sizes are kept)",
        )
    )
    meta = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in items)
    return (
        '<header class="masthead"><p class="kicker">Polygraph for MCP</p>'
        f'<h1>{_e(title)}</h1><dl class="meta">{meta}</dl></header>'
    )


def _nav() -> str:
    links = (
        ("summary", "Summary"),
        ("outcomes", "Outcomes"),
        ("heatmap", "Tools and faults"),
        ("coverage", "Coverage"),
        ("claims", "Server claims"),
        ("runs", "Runs"),
        ("ledgers", "Ledgers"),
    )
    inner = " ".join(f'<a href="#{a}">{t}</a>' for a, t in links)
    return f'<nav class="toc" aria-label="Sections">{inner}</nav>'


def _summary(results: ResultsFile) -> str:
    s = results.summary
    cards = [
        _rate_card(
            "Silent lie",
            OUTCOME_HELP[Outcome.SILENT_LIE],
            s.silent_lie,
            tone_when_found="bad",
            key="silent_lie",
        ),
        _rate_card(
            "Double action",
            "Did a one-time action twice. Counted over lost or duplicated deliveries.",
            s.double_action,
            tone_when_found="bad",
            key="double_action",
        ),
        _rate_card(
            "Hijacked",
            "Followed a planted instruction. Counted over hostile faults.",
            s.hijack,
            tone_when_found="bad",
            key="hijack",
        ),
        _rate_card(
            "Success",
            "Finished the task correctly despite the fault.",
            s.success,
            tone_when_found="good",
            key="success",
        ),
        _rate_card("Loop", OUTCOME_HELP[Outcome.LOOP], s.loop, tone_when_found="warn", key="loop"),
        _rate_card(
            "Crash", OUTCOME_HELP[Outcome.CRASH], s.crash, tone_when_found="warn", key="crash"
        ),
    ]
    pk = s.pass_k
    if pk is not None and pk.groups:
        cards.append(
            _plain_card(
                f"pass^{pk.k}",
                _pct(pk.passed / pk.groups),
                f"{pk.passed} of {pk.groups} groups",
                f"Groups where all of the first {pk.k} repeats recovered.",
            )
        )
    else:
        k = pk.k if pk is not None else 1
        cards.append(
            _plain_card(f"pass^{k}", "n/a", "no groups", "Needs repeats of the same test.")
        )
    if s.cost_multiplier is not None:
        cards.append(
            _plain_card(
                "Cost under faults",
                f"×{s.cost_multiplier:.2f}",
                "tokens, compared with control",
                "Mean tokens of faulted runs divided by the mean of control runs.",
            )
        )
    cov = results.coverage
    if cov.applicable:
        cov_value = f"{cov.covered} of {cov.applicable}"
        cov_detail = f"{_pct(cov.covered / cov.applicable)} of pairs tested"
    else:
        cov_value, cov_detail = "n/a", "no pairs apply"
    cards.append(
        _plain_card(
            "Coverage",
            cov_value,
            cov_detail,
            "Tool and fault pairs that got all the runs they need.",
            tone="info",
        )
    )
    control = s.runs - s.faulted_runs
    lead = (
        f"{s.runs} runs: {s.faulted_runs} under a fault and {control} control "
        f"run{'' if control == 1 else 's'} with no fault. Rates count faulted runs only."
    )
    return (
        '<section id="summary"><h2>Summary</h2>'
        f'<p class="lead">{_e(lead)}</p><div class="cards">{"".join(cards)}</div></section>'
    )


def _outcomes(results: ResultsFile) -> str:
    counts = {o: results.summary.outcomes.get(o, 0) for o in Outcome}
    total = sum(counts.values())
    segs: list[str] = []
    rows: list[str] = []
    for outcome, alpha in _BAR_ORDER:
        n = counts[outcome]
        share = n / total if total else 0.0
        label = OUTCOME_LABEL[outcome]
        if n:
            segs.append(
                f'<span class="seg t-{_tone(outcome)}" style="flex-grow:{n};--a:{alpha}" '
                f'title="{_e(label)}: {n}"></span>'
            )
        rows.append(
            f'<tr><th scope="row"><span class="swatch t-{_tone(outcome)}" '
            f'style="--a:{alpha}" aria-hidden="true"></span>{chip(outcome)}</th>'
            f'<td class="num">{n}</td><td class="num">{_pct(share) if total else "n/a"}</td>'
            f'<td class="muted">{_e(OUTCOME_HELP[outcome])}</td></tr>'
        )
    bar = (
        f'<div class="bar" aria-hidden="true">{"".join(segs)}</div>'
        if total
        else '<p class="muted">No runs yet.</p>'
    )
    return (
        '<section id="outcomes"><h2>Outcomes</h2>'
        '<p class="lead">Every run ends in one outcome. Each count is listed below the bar.</p>'
        f"{bar}"
        '<table class="plain outcomes"><caption class="sr">Runs per outcome</caption>'
        '<thead><tr><th scope="col">Outcome</th><th scope="col" class="num">Runs</th>'
        '<th scope="col" class="num">Share</th><th scope="col">Meaning</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _tool_keys(results: ResultsFile) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for t in results.tools:
        k = (t.server, t.name)
        if k not in seen:
            seen.add(k)
            keys.append(k)
    extra = {(p.server, p.tool) for p in results.coverage.pairs}
    extra |= {(r.fault.server, r.fault.tool) for r in results.runs if r.fault is not None}
    keys.extend(sorted(extra - seen))
    return keys


def _fault_groups() -> list[tuple[FaultFamily, list[FaultName]]]:
    groups: list[tuple[FaultFamily, list[FaultName]]] = []
    for fault in FaultName:
        family = FAULT_FAMILY[fault]
        if groups and groups[-1][0] is family:
            groups[-1][1].append(fault)
        else:
            groups.append((family, [fault]))
    return groups


def _heatmap(results: ResultsFile) -> str:
    stats: dict[tuple[str, str, FaultName], Counter[Outcome]] = {}
    for run in results.runs:
        f = run.fault
        if f is None:
            continue
        stats.setdefault((f.server, f.tool, f.name), Counter())[run.outcome] += 1
    planned = {(p.server, p.tool, p.fault): p for p in results.coverage.pairs}
    tools = _tool_keys(results)
    groups = _fault_groups()

    head1 = '<th scope="col" rowspan="2" class="corner">Tool</th>' + "".join(
        f'<th scope="colgroup" colspan="{len(fs)}" class="fam gstart" '
        f'title="{_e(FAMILY_LABEL[fam][1])}">{_e(FAMILY_LABEL[fam][0])}</th>'
        for fam, fs in groups
    )
    head2 = "".join(
        f'<th scope="col" class="f{" gstart" if i == 0 else ""}"><span><code>'
        f"{_e(f.value)}</code></span></th>"
        for _, fs in groups
        for i, f in enumerate(fs)
    )
    cols = "".join(f'<colgroup span="{len(fs)}"></colgroup>' for _, fs in groups)
    body: list[str] = []
    for server, tool in tools:
        cells: list[str] = []
        for _, fs in groups:
            for i, fault in enumerate(fs):
                cells.append(_heat_cell(server, tool, fault, stats, planned, gstart=i == 0))
        body.append(
            f'<tr><th scope="row"><code class="tool">{_e(server)}/{_e(tool)}</code></th>'
            f"{''.join(cells)}</tr>"
        )
    if not tools:
        return (
            '<section id="heatmap"><h2>Tools and faults</h2>'
            '<p class="muted">No tools were seen.</p></section>'
        )
    legend = (
        '<ul class="legend">'
        '<li><span class="key c-fine"></span>runs, none bad</li>'
        '<li><span class="key c-warn" style="--a:.35"></span>loop or crash</li>'
        '<li><span class="key c-bad" style="--a:.45"></span>silent lie, double action or '
        "hijack (darker means a bigger share)</li>"
        '<li><span class="key c-untested"></span>applies, not tested yet</li>'
        '<li><span class="key c-na"></span>does not apply</li></ul>'
    )
    return (
        '<section id="heatmap"><h2>Tools and faults</h2>'
        '<p class="lead">Each cell shows <strong>bad runs / runs</strong> for one tool under '
        "one fault. Control runs are left out. Hover a cell for the full breakdown.</p>"
        f'{legend}<div class="scroll"><table class="heat">'
        '<caption class="sr">Bad runs per tool and fault</caption>'
        f"<colgroup></colgroup>{cols}<thead><tr>{head1}</tr><tr>{head2}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )


def _heat_cell(
    server: str,
    tool: str,
    fault: FaultName,
    stats: dict[tuple[str, str, FaultName], Counter[Outcome]],
    planned: dict[tuple[str, str, FaultName], CoveragePair],
    *,
    gstart: bool,
) -> str:
    counter = stats.get((server, tool, fault))
    where = f"{server}/{tool} × {fault.value}"
    g = " gstart" if gstart else ""
    if not counter:
        if (server, tool, fault) in planned:
            need = planned[(server, tool, fault)].runs_required
            tip = f"{where}: applies, not tested yet (needs {need} runs)"
            return (
                f'<td class="c-untested{g}" title="{_e(tip)}"><span class="sr">{_e(tip)}</span>'
                "</td>"
            )
        tip = f"{where}: does not apply"
        return f'<td class="c-na{g}" title="{_e(tip)}"><span class="sr">does not apply</span></td>'
    runs = sum(counter.values())
    bad = sum(n for o, n in counter.items() if o in BAD_OUTCOMES)
    severe = sum(n for o, n in counter.items() if o in SEVERE)
    warn = sum(n for o, n in counter.items() if o in (Outcome.LOOP, Outcome.CRASH))
    breakdown = ", ".join(f"{OUTCOME_LABEL[o]} {counter[o]}" for o in Outcome if counter.get(o))
    tip = f"{where}: {runs} run{'s' if runs != 1 else ''}, {bad} bad ({breakdown})"
    if severe:
        cls, style = "c-bad", f' style="--a:{0.18 + 0.37 * severe / runs:.2f}"'
    elif warn:
        cls, style = "c-warn", f' style="--a:{0.15 + 0.3 * warn / runs:.2f}"'
    else:
        cls, style = "c-fine", ""
    return (
        f'<td class="{cls}{g}"{style} title="{_e(tip)}" data-bad="{bad}" data-runs="{runs}">'
        f'<span aria-hidden="true">{bad}/{runs}</span><span class="sr">{_e(tip)}</span></td>'
    )


def _hint(h: HintValue) -> str:
    word = "yes" if h.value else "no"
    return f'{word} <span class="src src-{h.source.value}">{_e(h.source.value)}</span>'


def _coverage(results: ResultsFile) -> str:
    cov = results.coverage
    if cov.applicable:
        ratio = cov.covered / cov.applicable
        head = (
            f'<p class="big"><strong>{cov.covered}/{cov.applicable}</strong> tool and fault pairs '
            f"covered ({_pct(ratio)}).</p>"
            f'<div class="meter" aria-hidden="true"><span style="width:{ratio * 100:.1f}%">'
            "</span></div>"
        )
    else:
        head = '<p class="big">No tool and fault pairs apply yet.</p>'

    todo = [p for p in cov.pairs if not p.covered]
    if todo:
        items = []
        for p in todo[:_MAX_UNTESTED]:
            top = ""
            if p.priority is Priority.TOP:
                top = ' <span class="badge-top">top priority</span>'
            items.append(
                f'<li><code class="tool">{_e(p.server)}/{_e(p.tool)}</code> × '
                f'<code class="fault">{_e(p.fault.value)}</code>{top} '
                f'<span class="muted">{p.runs_done} of {p.runs_required} runs done</span></li>'
            )
        more = (
            f'<p class="muted">And {len(todo) - _MAX_UNTESTED} more.</p>'
            if len(todo) > _MAX_UNTESTED
            else ""
        )
        untested = (
            '<h3>Not tested yet</h3><p class="muted">Riskiest first.</p>'
            f'<ol class="untested">{"".join(items)}</ol>{more}'
        )
    elif cov.pairs:
        untested = "<p>Every planned pair got the runs it needs.</p>"
    else:
        untested = ""

    if results.tools:
        rows = "".join(
            f'<tr><th scope="row"><code class="tool">{_e(t.key)}</code></th>'
            f"<td>{_hint(t.hints.read_only)}</td><td>{_hint(t.hints.destructive)}</td>"
            f"<td>{_hint(t.hints.idempotent)}</td><td>{_hint(t.hints.open_world)}</td></tr>"
            for t in results.tools
        )
        src_help = " · ".join(f"<strong>{k}</strong>: {v}" for k, v in _SOURCE_HELP)
        labels = (
            "<h3>Tool labels</h3>"
            '<p class="muted">How each tool was labelled, and where the label came from. '
            f"{src_help}.</p>"
            '<div class="scroll"><table class="plain labels"><caption class="sr">Tool labels'
            '</caption><thead><tr><th scope="col">Tool</th><th scope="col">Read only</th>'
            '<th scope="col">Destructive</th><th scope="col">Idempotent</th>'
            f'<th scope="col">Open world</th></tr></thead><tbody>{rows}</tbody></table></div>'
        )
    else:
        labels = ""
    return f'<section id="coverage"><h2>Coverage</h2>{head}{untested}{labels}</section>'


def _claims(results: ResultsFile, run_ids: set[str]) -> str:
    claims = results.server_claims
    if not claims:
        return (
            '<section id="claims"><h2>Server claims</h2>'
            '<p class="muted">No server claims were checked.</p></section>'
        )
    rows = "".join(_claim_row(c, run_ids) for c in claims)
    failed = sum(1 for c in claims if not c.held)
    lead = (
        f"Polygraph also checks what the tools say about themselves. "
        f"{failed} of {len(claims)} claims did not hold."
    )
    return (
        '<section id="claims"><h2>Server claims</h2>'
        f'<p class="lead">{_e(lead)}</p><div class="scroll"><table class="plain claims">'
        '<caption class="sr">Server claim checks</caption><thead><tr>'
        '<th scope="col">Tool</th><th scope="col">Claim</th><th scope="col">Held</th>'
        '<th scope="col">Evidence</th><th scope="col">Run</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _claim_row(c: ClaimCheck, run_ids: set[str]) -> str:
    claim = (
        "idempotent (safe to repeat)" if c.claim == "idempotent" else "read only (changes nothing)"
    )
    held = (
        '<span class="chip t-good">held</span>'
        if c.held
        else '<span class="chip t-bad">did not hold</span>'
    )
    if c.run_id and c.run_id in run_ids:
        link = f'<a href="#run-{_e(c.run_id)}">open ledger</a>'
    elif c.run_id:
        link = f"<code>{_e(c.run_id)}</code>"
    else:
        link = '<span class="muted">none</span>'
    cls = "" if c.held else ' class="failed"'
    return (
        f'<tr{cls}><th scope="row"><code class="tool">{_e(c.server)}/{_e(c.tool)}</code></th>'
        f"<td>{_e(claim)}</td><td>{held}</td><td>{_e(c.evidence)}</td><td>{link}</td></tr>"
    )


def sort_runs(runs: Iterable[Run]) -> list[Run]:
    """Bad outcomes first, then by outcome severity, keeping file order otherwise."""
    return sorted(
        runs, key=lambda r: (r.outcome not in BAD_OUTCOMES, -OUTCOME_PRECEDENCE[r.outcome])
    )


def _runs_table(runs: list[Run]) -> str:
    if not runs:
        return '<section id="runs"><h2>Runs</h2><p class="muted">No runs.</p></section>'
    counts = Counter(r.outcome for r in runs)
    options = ['<option value="all">All outcomes</option>', '<option value="bad">Bad only</option>']
    options += [
        f'<option value="{o.value}">{_e(OUTCOME_LABEL[o])} ({counts[o]})</option>'
        for o in Outcome
        if counts.get(o)
    ]
    rows: list[str] = []
    for r in runs:
        reasons = (
            '<ul class="reasons">' + "".join(f"<li>{_e(x)}</li>" for x in r.reasons) + "</ul>"
            if r.reasons
            else '<span class="muted">none</span>'
        )
        bad = "1" if r.outcome in BAD_OUTCOMES else "0"
        task = _e(r.task) if r.task else '<span class="muted">none</span>'
        rows.append(
            f'<tr data-outcome="{r.outcome.value}" data-bad="{bad}">'
            f'<th scope="row"><code class="test">{_e(r.test_id)}</code></th>'
            f'<td>{task}</td><td>{_fault_text(r)}</td><td class="num">{r.repeat}</td>'
            f"<td>{chip(r.outcome)}</td><td>{reasons}</td>"
            f'<td class="num">{_e(_duration(r.duration_ms))}</td>'
            f'<td><a href="#run-{_e(r.id)}">ledger</a></td></tr>'
        )
    return (
        '<section id="runs"><h2>Runs</h2>'
        '<p class="lead">Bad outcomes come first. Open a ledger to see what was asked, '
        "what happened on the server and what the agent was told.</p>"
        '<div id="filter-box" class="filter" hidden><label for="outcome-filter">Show</label> '
        f'<select id="outcome-filter">{"".join(options)}</select> '
        '<span id="filter-count" class="muted" aria-live="polite"></span></div>'
        '<div class="scroll"><table class="plain runs" id="runs-table">'
        '<caption class="sr">All runs</caption><thead><tr>'
        '<th scope="col">Test</th><th scope="col">Task</th><th scope="col">Fault</th>'
        '<th scope="col" class="num">Repeat</th><th scope="col">Outcome</th>'
        '<th scope="col">Reasons</th><th scope="col" class="num">Time</th>'
        '<th scope="col"><span class="sr">Ledger link</span></th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _forwarded(n: int) -> str:
    if n == 0:
        return '<p class="fwd">Did not reach the server</p>'
    if n == 1:
        return '<p class="fwd">Reached the server once</p>'
    return f'<p class="fwd many"><strong>Reached the server {n}×</strong></p>'


def _entry(e: LedgerEntry) -> str:
    bits = [f'<span class="seq">#{e.seq}</span>', f"<code>{_e(e.method)}</code>"]
    if e.tool:
        bits.append(f'<code class="tool">{_e(e.server)}/{_e(e.tool)}</code>')
    else:
        bits.append(f'<span class="muted">server <code>{_e(e.server)}</code></span>')
    if e.protocol_version:
        bits.append(f'<span class="muted">MCP {_e(e.protocol_version)}</span>')
    bits.append(f'<span class="muted">{_e(_duration(e.latency_ms))}</span>')
    mark = ""
    cls = "entry"
    attr = ""
    if e.fault is not None:
        cls += " faulted"
        attr = f' data-fault="{_e(e.fault.value)}"'
        mark = (
            '<p class="fault-mark">Fault injected here: '
            f'<code class="fault">{_e(e.fault.value)}</code></p>'
        )

    happened = _forwarded(e.happened.forwarded) + payload(
        e.happened.server_result, missing="no server result recorded"
    )
    if e.happened.server_is_error:
        happened += '<p class="flag">The server returned an error.</p>'
    if e.happened.note:
        happened += f'<p class="note">{_e(e.happened.note)}</p>'

    told = payload(e.told, missing="no answer")
    if e.told_is_error:
        told += '<p class="flag">The agent was told this is an error.</p>'

    return (
        f'<div class="{cls}"{attr}><div class="entry-head">{" ".join(bits)}{mark}</div>'
        '<div class="cells">'
        f'<div class="cell asked"><p class="cell-label">Asked</p>{payload(e.asked)}</div>'
        f'<div class="cell happened"><p class="cell-label">Happened</p>{happened}</div>'
        f'<div class="cell told"><p class="cell-label">Told</p>{told}</div>'
        "</div></div>"
    )


def _ledger(run: Run) -> str:
    fault_params = ""
    if run.fault is not None and run.fault.params:
        fault_params = (
            f" <code>{_e(json.dumps(run.fault.params, sort_keys=True, default=str))}</code>"
        )
    meta: list[tuple[str, str]] = [
        ("Run", f"<code>{_e(run.id)}</code>"),
        ("Test", f'<code class="test">{_e(run.test_id)}</code>'),
    ]
    if run.task:
        meta.append(("Task", _e(run.task)))
    meta += [
        ("Fault", _fault_text(run) + fault_params),
        ("Seed", f"<code>{run.seed}</code>"),
        ("Repeat", str(run.repeat)),
        ("Started", _e(_when(run.started_at))),
        ("Time", _e(_duration(run.duration_ms))),
    ]
    if run.tokens is not None:
        meta.append(("Tokens", _e(f"{run.tokens.input:,} in, {run.tokens.output:,} out")))
    if run.protocol_version:
        meta.append(("MCP version", _e(run.protocol_version)))
    meta_html = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in meta)

    if run.ledger:
        entries = (
            '<div class="cols" aria-hidden="true"><p class="col asked">Asked</p>'
            '<p class="col happened">Happened</p><p class="col told">Told</p></div>'
            + "".join(_entry(e) for e in run.ledger)
        )
        columns_note = (
            '<p class="muted small">Asked: what the agent sent. Happened: what really happened '
            "on the server. Told: what the agent got back.</p>"
        )
    else:
        entries, columns_note = '<p class="muted">No calls were recorded.</p>', ""

    after: list[str] = [
        f'<div class="block"><h4>Final answer</h4>'
        f"{payload(run.final_answer, missing='no final answer')}</div>"
    ]
    if run.judge is not None:
        j = run.judge
        how = j.method + (f", {j.model}" if j.model else "")
        why = f'<p class="muted">{_e(j.rationale)}</p>' if j.rationale else ""
        after.append(
            '<div class="block"><h4>Judge</h4>'
            f"<p>Did the agent claim success? <strong>{_yes_no(j.claimed_success)}</strong> "
            f'<span class="muted">(checked by {_e(how)})</span></p>{why}</div>'
        )
    if run.state_checks:
        items = "".join(
            f'<li class="{"pass" if c.passed else "fail"}">'
            f'<span class="mark" aria-hidden="true">{"✓" if c.passed else "✗"}</span> '
            f"<strong>{'passed' if c.passed else 'failed'}</strong>: {_e(c.name)}"
            + (f' <span class="muted">({_e(c.detail)})</span>' if c.detail else "")
            + "</li>"
            for c in run.state_checks
        )
        after.append(
            f'<div class="block"><h4>State checks</h4><ul class="checks">{items}</ul></div>'
        )
    if run.second_opinion is not None:
        so = run.second_opinion
        detail = f' <span class="muted">{_e(so.detail)}</span>' if so.detail else ""
        after.append(
            f'<div class="block"><h4>Second opinion</h4><p><code>{_e(so.tool)}</code> '
            f"flagged a lie: <strong>{_yes_no(so.flagged_lie)}</strong>; agrees with "
            f"Polygraph: <strong>{_yes_no(so.agrees_with_polygraph)}</strong>.{detail}</p></div>"
        )
    if run.error:
        after.append(
            f'<div class="block"><h4>Agent error</h4><pre class="payload"><code>'
            f"{_e(run.error)}</code></pre></div>"
        )
    reasons = (
        "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in run.reasons) + "</ul>"
        if run.reasons
        else '<p class="muted">No reasons given.</p>'
    )
    after.append(
        f'<div class="block verdict t-{_tone(run.outcome)}"><h4>Verdict</h4>'
        f"<p>{chip(run.outcome)} {_e(OUTCOME_HELP[run.outcome])}</p>{reasons}</div>"
    )
    summary = (
        f'{chip(run.outcome)} <code class="test">{_e(run.test_id)}</code> '
        f'<span class="muted">{_fault_text(run)} · '
        f"repeat {run.repeat}</span>"
    )
    return (
        f'<details class="run" id="run-{_e(run.id)}"><summary>{summary}</summary>'
        f'<div class="run-body"><dl class="meta">{meta_html}</dl>{columns_note}'
        f'<div class="ledger">{entries}</div><div class="after">{"".join(after)}</div>'
        '<p class="back"><a href="#runs">Back to runs</a></p></div></details>'
    )


def _ledgers(runs: list[Run]) -> str:
    if not runs:
        return ""
    return (
        '<section id="ledgers"><h2>Ledgers</h2>'
        '<p class="lead">One ledger per run. Open one to read it call by call.</p>'
        f"{''.join(_ledger(r) for r in runs)}</section>"
    )


# --------------------------------------------------------------------------- #
# CSS and JS
# --------------------------------------------------------------------------- #

_CSS = """
:root{
--paper:#F2F5F3;--sheet:#FAFCFB;--ink:#18212B;--muted:#56636F;--rule:#D3DEDA;
--blue:#2450A6;--green:#2E7A5A;--crimson:#B3262E;--amber:#A86E12;
--blue-rgb:36 80 166;--green-rgb:46 122 90;--crimson-rgb:179 38 46;--amber-rgb:168 110 18;
--sans:"Schibsted Grotesk",ui-sans-serif,system-ui,sans-serif;
--mono:"JetBrains Mono",ui-monospace,monospace;--r:3px;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{
--paper:#121A20;--sheet:#18222A;--ink:#E3E9E6;--muted:#9AA8B2;--rule:#2C3B42;
--blue:#8AABEF;--green:#6CC49E;--crimson:#F08A8F;--amber:#E2AE57;
--blue-rgb:138 171 239;--green-rgb:108 196 158;--crimson-rgb:240 138 143;
--amber-rgb:226 174 87}}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 var(--sans);
-webkit-text-size-adjust:100%}
main{max-width:1200px;margin:0 auto;padding:28px 16px 72px}
h1,h2,h3,h4{font-weight:600;line-height:1.25;margin:0}
h1{font-size:30px;margin:2px 0 14px}
h2{font-size:21px;padding-bottom:6px;border-bottom:1px solid var(--rule);margin-bottom:12px}
h3{font-size:16px;margin:20px 0 6px}
h4{font-size:14px;margin-bottom:6px}
p{margin:0 0 8px}
section{margin-top:40px}
a{color:var(--blue);text-underline-offset:2px}
a:focus-visible,summary:focus-visible,select:focus-visible{outline:2px solid var(--blue);
outline-offset:2px}
code,pre,.mono{font-family:var(--mono);font-size:.86em}
code{overflow-wrap:anywhere}
.scroll code{overflow-wrap:normal}
.muted{color:var(--muted)}
.small{font-size:13px}
.lead{color:var(--muted);max-width:72ch}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip:rect(0 0 0 0);white-space:nowrap;border:0}
.kicker{color:var(--blue);font-weight:600;margin:0}
.masthead{border-bottom:2px solid var(--ink);padding-bottom:16px}
dl.meta{display:flex;flex-wrap:wrap;gap:6px 22px;margin:0}
dl.meta div{display:flex;flex-wrap:wrap;gap:0 6px;align-items:baseline;min-width:0}
dl.meta dt{color:var(--muted)}
dl.meta dd{margin:0;min-width:0;overflow-wrap:anywhere}
.toc{display:flex;flex-wrap:wrap;gap:4px 16px;padding:10px 0;border-bottom:1px solid var(--rule)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.card{background:var(--sheet);border:1px solid var(--rule);border-left:4px solid var(--rule);
border-radius:var(--r);padding:12px 14px}
.card .label{font-weight:600;margin:0}
.card .v{font-size:28px;font-weight:600;margin:2px 0 0;font-variant-numeric:tabular-nums}
.card .detail{margin:0;font-variant-numeric:tabular-nums}
.card .ci,.card .help{color:var(--muted);font-size:13px;margin:4px 0 0}
.card.t-bad{border-left-color:var(--crimson)}
.card.t-warn{border-left-color:var(--amber)}
.card.t-good{border-left-color:var(--green)}
.card.t-info{border-left-color:var(--blue)}
.chip{--c:var(--muted);--c-rgb:86 99 111;display:inline-flex;align-items:center;gap:6px;
padding:0 8px;border:1px solid rgb(var(--c-rgb)/.55);background:rgb(var(--c-rgb)/.1);
border-radius:var(--r);font-size:13px;line-height:1.6;white-space:nowrap;color:var(--ink)}
.chip::before{content:"";width:8px;height:8px;border-radius:1px;background:var(--c)}
.t-good{--c:var(--green);--c-rgb:var(--green-rgb)}
.t-warn{--c:var(--amber);--c-rgb:var(--amber-rgb)}
.t-bad{--c:var(--crimson);--c-rgb:var(--crimson-rgb)}
.bar{display:flex;gap:2px;height:20px;border-radius:var(--r);overflow:hidden;margin:6px 0 14px}
.seg{display:block;flex-basis:0;min-width:3px;background:rgb(var(--c-rgb)/var(--a,1))}
.swatch{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:8px;
vertical-align:-1px;background:rgb(var(--c-rgb)/var(--a,1))}
.outcomes .chip::before{display:none}
.scroll{position:relative;overflow-x:auto;-webkit-overflow-scrolling:touch}
table.plain{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
table.plain th,table.plain td{text-align:left;vertical-align:top;padding:7px 10px 7px 0;
border-bottom:1px solid var(--rule)}
table.plain thead th{font-weight:600;border-bottom:1px solid var(--ink)}
table.plain tbody th{font-weight:400}
.num{text-align:right!important;white-space:nowrap}
.reasons{margin:0;padding-left:18px}
tr.failed th,tr.failed td{background:rgb(var(--crimson-rgb)/.07)}
tr.failed th{box-shadow:inset 3px 0 0 var(--crimson);padding-left:8px}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;list-style:none;padding:0;margin:0 0 10px;
color:var(--muted);font-size:13px}
.legend li{display:flex;align-items:center;gap:6px}
.key{display:inline-block;width:16px;height:12px;border-radius:2px}
table.heat{border-collapse:separate;border-spacing:3px;font-variant-numeric:tabular-nums}
.heat th.corner{text-align:left;vertical-align:bottom}
.heat th.fam{font-weight:600;font-size:13px;text-align:left;padding:2px 4px;
border-bottom:1px solid var(--ink)}
.heat th.f{vertical-align:bottom;padding:4px 0;font-weight:400}
.heat th.f span{display:inline-block;writing-mode:vertical-rl;transform:rotate(180deg);
white-space:nowrap;font-size:12px}
.heat tbody th{text-align:left;font-weight:400;padding-right:10px;white-space:nowrap}
.heat td{width:46px;min-width:46px;height:34px;text-align:center;border-radius:2px;
font-family:var(--mono);font-size:12px}
.heat .gstart{border-left:6px solid var(--paper);background-clip:padding-box}
.c-na{background:transparent}
.c-untested{box-shadow:inset 0 0 0 1px var(--muted)}
.c-fine{background:rgb(var(--green-rgb)/.18)}
.c-warn{background:rgb(var(--amber-rgb)/var(--a,.3))}
.c-bad{background:rgb(var(--crimson-rgb)/var(--a,.4))}
.key.c-na{box-shadow:inset 0 0 0 1px var(--rule)}
.big{font-size:17px}
.meter{height:8px;background:var(--rule);border-radius:var(--r);overflow:hidden;max-width:480px;
margin:4px 0 8px}
.meter span{display:block;height:100%;background:var(--blue)}
ol.untested{padding-left:22px;margin:0}
ol.untested li{padding:3px 0}
.badge-top{display:inline-block;padding:0 6px;border:1px solid var(--blue);color:var(--blue);
border-radius:var(--r);font-size:12px;margin-left:4px}
.src{color:var(--muted);font-size:12px;margin-left:4px}
.src-default,.src-guessed{font-style:italic}
.filter{margin:6px 0 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
select{font:inherit;color:var(--ink);background:var(--sheet);border:1px solid var(--rule);
border-radius:var(--r);padding:3px 6px}
details.run{border:1px solid var(--rule);border-radius:var(--r);background:var(--sheet);
margin:8px 0}
details.run>summary{cursor:pointer;padding:9px 12px;display:flex;flex-wrap:wrap;gap:6px 10px;
align-items:center}
details.run[open]>summary{border-bottom:1px solid var(--rule)}
details.run:target{border-color:var(--blue);box-shadow:0 0 0 1px var(--blue)}
.run-body{padding:12px;min-width:0}
.entry,.cells{min-width:0}
.run-body dl.meta{margin-bottom:10px}
.cols,.cells{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 16px}
.cols .col{font-weight:600;margin:0;padding-bottom:4px;border-bottom:2px solid var(--rule)}
.cols .col.asked{color:var(--blue);border-color:var(--blue)}
.cols .col.happened{border-color:var(--muted)}
.cols .col.told{border-color:var(--ink)}
.entry{padding:10px 8px;margin:0 -8px;border-bottom:1px solid var(--rule);border-radius:var(--r)}
.entry-head{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;margin-bottom:6px}
.seq{font-weight:600;font-variant-numeric:tabular-nums}
.entry.faulted{background:rgb(var(--blue-rgb)/.06);outline:1px dashed var(--blue)}
.fault-mark{margin:0;color:var(--blue);font-weight:600}
.cell{min-width:0;padding-left:10px;border-left:2px solid var(--rule)}
.cell.asked{border-left-color:var(--blue)}
.cell.told{border-left-color:var(--ink)}
.cell-label{display:none;font-weight:600;margin-bottom:4px}
.cell.asked .cell-label{color:var(--blue)}
pre.payload{margin:0 0 4px;padding:8px 10px;background:var(--paper);border:1px solid var(--rule);
border-radius:var(--r);white-space:pre-wrap;word-break:break-word;max-height:24em;overflow:auto}
.hash{margin:0}
.none{color:var(--muted);font-style:italic}
.fwd{margin-bottom:6px}
.flag{font-weight:600;margin-top:4px}
.note{color:var(--muted);font-style:italic;margin-top:4px}
.after{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px 20px;
margin-top:14px}
.block{border-top:1px solid var(--ink);padding-top:8px;min-width:0}
.verdict{border-top-width:3px;border-top-color:var(--c)}
.checks{list-style:none;padding:0;margin:0}
.checks .pass .mark{color:var(--green)}
.checks .fail .mark{color:var(--amber)}
.back{margin:14px 0 0}
footer{margin-top:56px;padding-top:12px;border-top:1px solid var(--rule);color:var(--muted);
font-size:13px}
@media (max-width:760px){
h1{font-size:24px}
.cols{display:none}
.cells{grid-template-columns:1fr}
.cell-label{display:block}
}
@media print{details.run{break-inside:avoid}.toc,.filter{display:none}}
"""

_JS = """(function(){
var box=document.getElementById('filter-box');
var sel=document.getElementById('outcome-filter');
var out=document.getElementById('filter-count');
var table=document.getElementById('runs-table');
if(box&&sel&&table){
var rows=table.tBodies[0].rows;
var apply=function(){var v=sel.value,shown=0;
for(var i=0;i<rows.length;i++){var r=rows[i];
var ok=v==='all'||(v==='bad'?r.getAttribute('data-bad')==='1':r.getAttribute('data-outcome')===v);
r.hidden=!ok;if(ok){shown++;}}
out.textContent=shown+' of '+rows.length+' runs shown';};
box.hidden=false;sel.addEventListener('change',apply);apply();}
var openHash=function(){var id=location.hash.slice(1);if(!id){return;}
var el=document.getElementById(id);
if(el&&el.tagName==='DETAILS'){el.open=true;el.scrollIntoView();}};
window.addEventListener('hashchange',openHash);openHash();
})();"""


def _csp(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    return (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{digest}'; img-src data:; base-uri 'none'; form-action 'none'"
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def render_html(results: ResultsFile, *, title: str = "Polygraph report") -> str:
    """Render the whole report as one self-contained HTML document."""
    runs = sort_runs(results.runs)
    run_ids = {r.id for r in runs}
    body = "".join(
        (
            _header(results, title),
            _nav(),
            _summary(results),
            _outcomes(results),
            _heatmap(results),
            _coverage(results),
            _claims(results, run_ids),
            _runs_table(runs),
            _ledgers(runs),
        )
    )
    footer = (
        f"<footer>Made by pytest-polygraph {_e(results.environment.polygraph_version)} "
        f"from a {_e(results.schema_)} file. This page makes no network requests.</footer>"
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{_csp(_JS)}">'
        '<meta name="referrer" content="no-referrer">'
        f'<meta name="generator" content="pytest-polygraph">'
        f"<title>{_e(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}{footer}</main><script>{_JS}</script></body></html>\n"
    )
