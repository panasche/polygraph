"""Pin everything a run depends on (research doc §9), into ``pins/<date>.json``.

Captured:

- **lockfile hash**: sha256 of ``uv.lock`` (benchmark folder or repo root) if there
  is one, plus sha256 of this environment's ``pip freeze`` (always);
- **git**: commit SHA, branch, and whether the working tree had changes;
- **versions**: Python, pytest-polygraph, every framework, the MCP SDKs;
- **models**: the model ids under test and the judge model (ids only, no keys);
- **protocol versions**: from results files (``environment.protocol_versions``) and,
  with ``--probe``, a live check of each installed framework (free, no model);
- **prompt**: sha256 of the shared system prompt; and the seed.

There is no Docker image in the benchmark itself (frameworks run in local
virtualenvs), so ``docker_image_digest`` stays empty unless you pass one.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bench import BENCH_ROOT
from bench.prompt import SYSTEM_PROMPT, prompt_sha256

IST = timezone(timedelta(hours=5, minutes=30), "IST")

DISTRIBUTIONS = (
    "pytest-polygraph",
    "langgraph",
    "langchain",
    "langchain-core",
    "langchain-mcp-adapters",
    "langchain-openai",
    "langchain-anthropic",
    "openai-agents",
    "openai",
    "pydantic-ai-slim",
    "fastmcp-slim",
    "smolagents",
    "mcpadapt",
    "mcp",
    "mcp-types",
    "agent-polygraph",
    "httpx",
    "pytest",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: Sequence[str], cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.run(
            list(cmd), cwd=cwd, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def git_state(root: Path = BENCH_ROOT) -> dict[str, Any]:
    sha = (_run(["git", "rev-parse", "HEAD"], root) or "").strip() or None
    branch = (_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root) or "").strip() or None
    status = _run(["git", "status", "--porcelain"], root)
    return {"sha": sha, "branch": branch, "dirty": None if status is None else bool(status.strip())}


def versions(dists: Iterable[str] = DISTRIBUTIONS) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in dists:
        try:
            out[d] = importlib.metadata.version(d)
        except importlib.metadata.PackageNotFoundError:
            continue
    return out


def lockfiles(root: Path = BENCH_ROOT) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for candidate in (root / "uv.lock", root.parent / "uv.lock"):
        if candidate.exists():
            out["uv_lock"] = {
                "path": str(candidate.relative_to(root.parent)),
                "sha256": sha256_file(candidate),
            }
            break
    freeze = _run([sys.executable, "-m", "pip", "freeze", "--exclude-editable"])
    if freeze is not None:
        lines = sorted(line for line in freeze.splitlines() if line.strip())
        out["pip_freeze_sha256"] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        out["pip_freeze_packages"] = len(lines)
    return out


def protocol_versions_from_results(paths: Iterable[Path]) -> dict[str, list[str]]:
    from polygraph.results import parse

    out: dict[str, set[str]] = {}
    for p in paths:
        r = parse(p.read_text(encoding="utf-8"))
        key = r.environment.framework or p.stem
        out.setdefault(key, set()).update(r.environment.protocol_versions)
    return {k: sorted(v) for k, v in out.items()}


def capture(
    *,
    models: Sequence[str] = (),
    judge_model: str | None = None,
    seed: int | None = None,
    results: Sequence[Path] = (),
    probe: bool = False,
    docker_image_digest: str | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    e = env if env is not None else os.environ
    when = (now or datetime.now(IST)).astimezone(IST)
    model_ids = list(models) or (
        [e["POLYGRAPH_BENCH_MODEL"]] if e.get("POLYGRAPH_BENCH_MODEL") else []
    )
    data: dict[str, Any] = {
        "schema": "polygraph-bench-pins/v1",
        "captured_at": when.isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine().lower()}",
        "git": git_state(),
        "lockfiles": lockfiles(),
        "versions": versions(),
        "models": model_ids,
        "judge_model": judge_model or e.get("POLYGRAPH_BENCH_JUDGE_MODEL") or None,
        "seed": seed,
        "system_prompt_sha256": prompt_sha256(SYSTEM_PROMPT),
        "docker_image_digest": docker_image_digest,
        "protocol_versions": {
            "from_results": protocol_versions_from_results(results),
            "probed": {},
        },
    }
    if probe:
        from adapters import ADAPTERS, adapter_class
        from bench.probe import probe as run_probe

        for name in ADAPTERS:
            if adapter_class(name).available():
                r = run_probe(name)
                data["protocol_versions"]["probed"][name] = {
                    "framework_version": r.framework_version,
                    "protocol_versions": r.protocol_versions,
                    "error": r.error,
                }
    return data


def write(data: Mapping[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    day = str(data["captured_at"])[:10]
    path = out_dir / f"{day}.json"
    n = 1
    while path.exists():
        n += 1
        path = out_dir / f"{day}-{n}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
