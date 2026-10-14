"""How often does the judge agree with a human? Cohen's kappa and raw agreement.

    python -m judge.agreement labels.csv [--json]

Reads the CSV from :mod:`judge.label` after a human filled in ``human_label``,
looks up the judge's verdict for each run in the results file named on that row,
and prints:

- raw agreement over all three labels (yes / no / unclear);
- Cohen's kappa over the three labels;
- Cohen's kappa over the rows where both said yes or no (the claim was clear);
- the confusion table.

Rows with an empty or unreadable ``human_label`` are skipped and counted.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from polygraph.results import ResultsFile, parse

LABELS = ("yes", "no", "unclear")
_ALIASES = {
    "yes": "yes", "y": "yes", "true": "yes", "1": "yes", "claimed": "yes",
    "no": "no", "n": "no", "false": "no", "0": "no",
    "unclear": "unclear", "u": "unclear", "?": "unclear", "unsure": "unclear",
}  # fmt: skip


def normalise(label: str | None) -> str | None:
    return _ALIASES.get((label or "").strip().lower())


def from_verdict(claimed: bool | None) -> str:
    return {True: "yes", False: "no", None: "unclear"}[claimed]


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa for two raters over the same items (``None`` if undefined)."""
    if len(a) != len(b):
        raise ValueError("both raters must label the same items")
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else None  # both raters used one label only
    return (po - pe) / (1 - pe)


@dataclass
class Agreement:
    labelled: int
    skipped: int
    raw_agreement: float | None
    kappa: float | None
    kappa_clear_only: float | None
    clear_items: int
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)  # human -> judge -> n
    judge_methods: dict[str, int] = field(default_factory=dict)


def compute(
    pairs: Sequence[tuple[str, str]], skipped: int = 0, methods: Counter[str] | None = None
) -> Agreement:
    human = [h for h, _ in pairs]
    judge = [j for _, j in pairs]
    n = len(pairs)
    raw = (sum(1 for h, j in pairs if h == j) / n) if n else None
    clear = [(h, j) for h, j in pairs if h != "unclear" and j != "unclear"]
    confusion = {h: {j: 0 for j in LABELS} for h in LABELS}
    for h, j in pairs:
        confusion[h][j] += 1
    return Agreement(
        labelled=n,
        skipped=skipped,
        raw_agreement=raw,
        kappa=cohen_kappa(human, judge),
        kappa_clear_only=cohen_kappa([h for h, _ in clear], [j for _, j in clear]),
        clear_items=len(clear),
        confusion=confusion,
        judge_methods=dict(methods or {}),
    )


def pairs_from_csv(path: Path) -> tuple[list[tuple[str, str]], int, Counter[str]]:
    cache: dict[str, ResultsFile] = {}
    pairs: list[tuple[str, str]] = []
    skipped = 0
    methods: Counter[str] = Counter()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            human = normalise(row.get("human_label"))
            if human is None:
                skipped += 1
                continue
            rf = row["results_file"]
            if rf not in cache:
                cache[rf] = parse(Path(rf).read_text(encoding="utf-8"))
            run = next((r for r in cache[rf].runs if r.id == row["run_id"]), None)
            if run is None:
                raise SystemExit(f"run {row['run_id']} not found in {rf}")
            verdict = run.judge.claimed_success if run.judge is not None else None
            methods[run.judge.method if run.judge is not None else "none"] += 1
            pairs.append((human, from_verdict(verdict)))
    return pairs, skipped, methods


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f} %"


def _num(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def render(a: Agreement) -> str:
    lines = [
        f"labelled runs:            {a.labelled} (skipped {a.skipped} without a human label)",
        "judge methods:            "
        + ", ".join(f"{k} {v}" for k, v in sorted(a.judge_methods.items())),
        f"raw agreement:            {_pct(a.raw_agreement)}",
        f"Cohen's kappa (3 labels): {_num(a.kappa)}",
        f"Cohen's kappa (yes/no):   {_num(a.kappa_clear_only)} over {a.clear_items} clear runs",
        "",
        "human \\ judge   " + "".join(f"{j:>9}" for j in LABELS),
    ]
    for h in LABELS:
        lines.append(f"{h:<16}" + "".join(f"{a.confusion[h][j]:>9}" for j in LABELS))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m judge.agreement", description=__doc__.split("\n")[0]
    )
    p.add_argument("csv", help="the labelled CSV from python -m judge.label")
    p.add_argument("--json", action="store_true")
    ns = p.parse_args(argv)
    pairs, skipped, methods = pairs_from_csv(Path(ns.csv))
    if not pairs:
        print("no labelled rows yet: fill in human_label (yes / no / unclear)", file=sys.stderr)
        return 2
    a = compute(pairs, skipped, methods)
    print(json.dumps(asdict(a), indent=2) if ns.json else render(a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
