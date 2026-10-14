"""Model check (research doc §7.6): the three headline faults, one framework, a stronger model.

Re-runs the tasks pinned to ``lost_response``, ``injection`` and
``description_poison`` (pytest marker ``model_check``) on one framework, with
the main model and a second, stronger one, and prints the bad-outcome rates
side by side. MCPTox found stronger models were often *more* open to tool
poisoning, so "just use a better model" is a claim to test, not assume.

    python model_check.py --framework pydantic-ai \\
        --model openai:<main-model-id> --strong-model openai:<stronger-model-id> --runs 10

    python model_check.py --compare out/<run>/results/a.json out/<run>/results/b.json

``--dry-run`` exercises the same path with the scripted agents (no models).
Every number printed comes from the results files; nothing is estimated.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bench import cli  # noqa: E402
from bench.pins import IST  # noqa: E402
from bench.tasks import MODEL_CHECK_FAULTS  # noqa: E402


def _rate(num: int, den: int, lo: float | None, hi: float | None) -> str:
    if den == 0:
        return "-"
    ci = f" [{lo * 100:.0f}-{hi * 100:.0f}]" if lo is not None and hi is not None else ""
    return f"{num}/{den} ({num / den * 100:.0f} %{ci})"


def compare(paths: Sequence[Path]) -> str:
    """A side-by-side table of the model-check faults, straight from results files."""
    from polygraph.metrics import summarize
    from polygraph.results import parse

    lines = []
    head = (
        f"{'model':<34} {'fault':<20} {'runs':>5}  "
        f"{'silent lie':<20} {'double action':<20} {'hijack':<20}"
    )
    lines += [head, "-" * len(head)]
    for path in paths:
        r = parse(path.read_text(encoding="utf-8"))
        model = r.environment.model or path.stem
        for fault in MODEL_CHECK_FAULTS:
            runs = [x for x in r.runs if x.fault is not None and x.fault.name.value == fault]
            if not runs:
                continue
            s = summarize(runs, r.coverage)
            rates = [
                _rate(x.numerator, x.denominator, x.ci_low, x.ci_high)
                for x in (s.silent_lie, s.double_action, s.hijack)
            ]
            lines.append(
                f"{model[:34]:<34} {fault:<20} {len(runs):>5}  "
                + " ".join(f"{x:<20}" for x in rates)
            )
    lines.append("(95 % Wilson intervals in brackets)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python model_check.py", description=__doc__.split("\n")[0])
    p.add_argument("--framework", default=None, help="one framework (dry run: a scripted one)")
    p.add_argument("--model", default=None, help="the main model, provider:model-id")
    p.add_argument("--strong-model", default=None, help="the stronger model, provider:model-id")
    p.add_argument("--runs", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--config", default="default")
    p.add_argument("--out", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--compare",
        nargs="+",
        default=None,
        metavar="RESULTS",
        help="only print the comparison for existing results files",
    )
    ns, extra = p.parse_known_args(argv)
    if ns.compare:
        print(compare([Path(x) for x in ns.compare]))
        return 0
    args = ["-m", "model_check", "--faults", ",".join(MODEL_CHECK_FAULTS), "--config", ns.config]
    if ns.runs is not None:
        args += ["--runs", str(ns.runs)]
    if ns.seed is not None:
        args += ["--seed", str(ns.seed)]
    out = (
        Path(ns.out)
        if ns.out
        else HERE / "out" / ("model-check-" + datetime.now(IST).strftime("%Y%m%d-%H%M%S"))
    )
    args += ["--out", str(out)]
    if ns.dry_run:
        args += ["--dry-run", "--framework", ns.framework or "scripted-naive"]
    else:
        if not (ns.framework and ns.model and ns.strong_model):
            p.error("--framework, --model and --strong-model are required (or use --dry-run)")
        args += ["--framework", ns.framework, "--model", ns.model, "--model", ns.strong_model]
    code = cli.main([*args, *extra])
    out_dirs = sorted((HERE / "out").glob("*")) if not ns.out else [Path(ns.out)]
    latest = out_dirs[-1] if out_dirs else None
    if latest is not None:
        files = sorted((latest / "results").glob("*.json"))
        if files:
            print("\nModel check:")
            print(compare(files))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
