"""Capture versions, hashes, model ids and protocol versions into pins/<date>.json.

Run it in each framework's virtualenv right before (and after) the paid runs:

    python pins/capture.py --model openai:<model-id> --seed 20261014 --probe
    python pins/capture.py --results out/<run>/results/*.json

``--probe`` checks which MCP protocol version every installed framework speaks,
for free (no model call). Model ids are recorded; API keys never are.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bench.pins import capture, write  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python pins/capture.py", description=__doc__.split("\n")[0])
    p.add_argument("--model", action="append", default=[], help="model id under test (repeatable)")
    p.add_argument("--judge-model", default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--results", nargs="*", default=[], help="results files to read protocol versions from"
    )
    p.add_argument("--probe", action="store_true", help="probe each installed framework (no model)")
    p.add_argument("--docker-image-digest", default=None)
    p.add_argument("--out-dir", default=str(HERE))
    ns = p.parse_args(argv)
    data = capture(
        models=ns.model,
        judge_model=ns.judge_model,
        seed=ns.seed,
        results=[Path(r) for r in ns.results],
        probe=ns.probe,
        docker_image_digest=ns.docker_image_digest,
    )
    path = write(data, Path(ns.out_dir))
    print(f"pinned to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
