"""Run the Polygraph benchmark matrix. See ``python run.py --help`` and README.md.

python run.py --dry-run                 # offline, free: scripted agents, all three worlds
python run.py --framework langgraph --model openai:<model-id> --runs 5
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
