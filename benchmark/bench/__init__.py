"""Core of the Polygraph benchmark harness.

- :mod:`bench.agent`    the one interface every framework adapter implements
- :mod:`bench.prompt`   the system prompt every framework gets (the same for all)
- :mod:`bench.models`   ``provider:model`` specs read from the environment
- :mod:`bench.configs`  config labels: ``default``, ``best-practice``, ``R1`` .. ``R7``
- :mod:`bench.settings` what the current pytest session is benchmarking (from env)
- :mod:`bench.costs`    the price table (``prices.toml``) and cost per run in tokens and ₹
- :mod:`bench.pins`     capture versions, hashes and protocol versions for a run
- :mod:`bench.probe`    check which MCP protocol version a framework speaks (no model needed)
- :mod:`bench.cli`      ``run.py`` / ``polygraph-bench``: the framework × config × model matrix
"""

from __future__ import annotations

__all__ = ["BENCH_ROOT"]

from pathlib import Path

#: the benchmark folder (holds pyproject.toml, tasks/, prices.toml, ...)
BENCH_ROOT = Path(__file__).resolve().parent.parent
