"""Sample runs from results files into a CSV for a human to label.

    python -m judge.label --results out/<run>/results/*.json --n 100 --seed 7 --out labels.csv

The CSV has one row per sampled run with the task and the agent's final answer.
Fill in ``human_label`` with ``yes``, ``no`` or ``unclear`` (did the agent
*claim* success?). The judge's own verdict is left out on purpose, so the
labeller is not nudged; :mod:`judge.agreement` looks it up later by run id.

Final answers are only in results files written with payload text
(``run.py`` does this by default; the data is invented). Runs without text are
skipped and counted.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from polygraph.results import ResultsFile, Run, parse

FIELDS = [
    "sample_id",
    "results_file",
    "run_id",
    "framework",
    "model",
    "config_label",
    "task",
    "fault",
    "final_answer",
    "human_label",
    "labeller_notes",
]


@dataclass
class Candidate:
    path: str
    results: ResultsFile
    run: Run


def load(paths: Iterable[str | Path]) -> list[tuple[str, ResultsFile]]:
    out = []
    for p in paths:
        path = Path(p)
        out.append((str(path), parse(path.read_text(encoding="utf-8"))))
    return out


def candidates(files: Sequence[tuple[str, ResultsFile]]) -> tuple[list[Candidate], int]:
    """Runs that have a final answer text, and the number skipped for having none."""
    found: list[Candidate] = []
    skipped = 0
    for path, results in files:
        for run in results.runs:
            if run.final_answer is None or run.final_answer.text is None:
                skipped += 1
                continue
            found.append(Candidate(path, results, run))
    return found, skipped


def sample(
    found: Sequence[Candidate], n: int, seed: int, *, stratify: bool = False
) -> list[Candidate]:
    """``n`` runs at random (``stratify`` spreads them evenly over outcomes)."""
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    if n >= len(found):
        picked = list(found)
        rng.shuffle(picked)
        return picked
    if not stratify:
        return rng.sample(list(found), n)
    groups: dict[str, list[Candidate]] = {}
    for c in found:
        groups.setdefault(c.run.outcome.value, []).append(c)
    for g in groups.values():
        rng.shuffle(g)
    picked: list[Candidate] = []
    keys = sorted(groups)
    while len(picked) < n:
        for k in keys:
            if groups[k] and len(picked) < n:
                picked.append(groups[k].pop())
    rng.shuffle(picked)
    return picked


def write_csv(rows: Sequence[Candidate], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for i, c in enumerate(rows, 1):
            env = c.results.environment
            fault = c.run.fault
            w.writerow(
                {
                    "sample_id": i,
                    "results_file": c.path,
                    "run_id": c.run.id,
                    "framework": env.framework or "",
                    "model": env.model or "",
                    "config_label": env.config_label or "",
                    "task": c.run.task or "",
                    "fault": f"{fault.name.value} on {fault.tool}" if fault else "none",
                    "final_answer": c.run.final_answer.text if c.run.final_answer else "",
                    "human_label": "",
                    "labeller_notes": "",
                }
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m judge.label", description=__doc__.split("\n")[0])
    p.add_argument(
        "--results", nargs="+", required=True, help="results files (globs expanded by your shell)"
    )
    p.add_argument("--n", type=int, default=100, help="how many runs to sample (default 100)")
    p.add_argument("--seed", type=int, default=1, help="sampling seed (write it down)")
    p.add_argument("--stratify", action="store_true", help="spread the sample over outcomes")
    p.add_argument("--out", required=True, help="CSV to write")
    ns = p.parse_args(argv)
    found, skipped = candidates(load(ns.results))
    if not found:
        print(
            "no runs with a final answer text: write results with --polygraph-include-content",
            file=sys.stderr,
        )
        return 2
    picked = sample(found, ns.n, ns.seed, stratify=ns.stratify)
    write_csv(picked, Path(ns.out))
    print(
        f"wrote {len(picked)} runs to {ns.out} (seed {ns.seed}; {skipped} runs had no answer text)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
