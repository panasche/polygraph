"""Cassettes and ``polygraph replay``.

Every run writes a local cassette, ``.polygraph/cassettes/<run_id>.jsonl``: a
header line (test id, fault, seed, repeat) and every ledger record *with
payload text*. Cassettes never leave the machine and are never uploaded.

``polygraph replay <run-id> [--times N]`` re-runs that pytest node with the same
fault and seed. The proxy re-serves the recorded tool responses (requests
still reach the server, so real state changes happen again), then the tally
says how often the original outcome came back. A scripted agent repeats
exactly; an LLM may choose differently, which is the point of ``--times``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .ledger import LedgerRecord

CASSETTE_VERSION = 1


def cassette_path(root: Path, run_id: str) -> Path:
    return root / ".polygraph" / "cassettes" / f"{run_id}.jsonl"


def write_cassette(path: Path, header: dict[str, Any], records: Sequence[LedgerRecord]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"cassette": CASSETTE_VERSION, **header}) + "\n")
        for r in records:
            fh.write(r.model_dump_json() + "\n")
    return path


def read_cassette(path: Path) -> tuple[dict[str, Any], list[LedgerRecord]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path} is empty")
    header = json.loads(lines[0])
    if header.get("cassette") != CASSETTE_VERSION:
        raise ValueError(f"{path} is not a polygraph cassette (version {header.get('cassette')})")
    records = [LedgerRecord.model_validate_json(x) for x in lines[1:] if x.strip()]
    return header, records


def replay(
    run_id: str,
    *,
    root: Path | None = None,
    times: int = 1,
    pytest_args: Sequence[str] = (),
    out: Callable[[str], None] = print,
    runner: Callable[[list[str], Path], int] | None = None,
) -> int:
    """Re-run one recorded run ``times`` times and print how often its outcome recurs."""
    root = (root or Path.cwd()).resolve()
    path = cassette_path(root, run_id)
    if not path.exists():
        out(f"No cassette for run {run_id} (looked in {path.parent}).")
        return 2
    header, _ = read_cassette(path)
    node = header["test_id"]
    rootdir = Path(header.get("rootdir") or root)
    original = header.get("outcome")
    fault = header.get("fault")
    label = (
        f"{fault['name']} on {fault['server']}/{fault['tool']}" if fault else "no fault (control)"
    )
    out(f"Replaying {node}")
    out(f"  fault: {label} · seed {header.get('seed')} · original outcome: {original}")
    tally: Counter[str] = Counter()
    run = runner or _run_pytest
    with tempfile.TemporaryDirectory(prefix="polygraph-replay-") as tmp:
        for i in range(max(1, times)):
            res = Path(tmp) / f"replay-{i}.json"
            args = [
                node,
                f"--polygraph-replay={path}",
                f"--polygraph-json={res}",
                "-q",
                "-p",
                "no:cacheprovider",
                *pytest_args,
            ]
            run(args, rootdir)
            outcome = _outcome_of(res)
            tally[outcome] += 1
            out(f"  replay {i + 1}: {outcome}")
    n = sum(tally.values())
    same = tally.get(str(original), 0)
    out(f"Outcome {original} came back {same}/{n} times ({same / n:.0%}).")
    others = ", ".join(f"{k} {v}" for k, v in tally.most_common() if k != original)
    if others:
        out(f"Other outcomes: {others}.")
    return 0


def _run_pytest(args: list[str], cwd: Path) -> int:
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3600,
        check=False,
    )
    return proc.returncode


def _outcome_of(path: Path) -> str:
    if not path.exists():
        return "not run"
    from .results import parse

    results = parse(path.read_text(encoding="utf-8"))
    if not results.runs:
        return "not run"
    return results.runs[-1].outcome.value
