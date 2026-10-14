"""Assemble and write a ``polygraph-results/v1`` file (always valid, summary from metrics)."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .metrics import summarize
from .results import (
    ClaimCheck,
    Coverage,
    Environment,
    GitInfo,
    ResultsFile,
    Run,
    ToolInfo,
    iter_payloads,
    parse,
)


def detect_ci(env: Mapping[str, str] | None = None) -> str:
    e = env if env is not None else os.environ
    if e.get("GITHUB_ACTIONS") == "true":
        return "github-actions"
    if e.get("GITLAB_CI"):
        return "gitlab-ci"
    if e.get("CI"):
        return "ci"
    return "local"


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def git_info(root: Path, env: Mapping[str, str] | None = None) -> GitInfo:
    e = env if env is not None else os.environ
    sha = e.get("GITHUB_SHA") or _git(root, "rev-parse", "HEAD")
    ref = e.get("GITHUB_REF")
    branch = (
        e.get("GITHUB_HEAD_REF")
        or e.get("GITHUB_REF_NAME")
        or _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    )
    pr = None
    if ref:
        m = re.match(r"refs/pull/(\d+)/", ref)
        if m:
            pr = int(m.group(1))
    repo = e.get("GITHUB_REPOSITORY")
    if sha and not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        sha = None
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        repo = None
    if branch == "HEAD":
        branch = None
    return GitInfo(
        sha=sha,
        branch=branch,
        ref=ref,
        pr_number=pr,
        base_branch=e.get("GITHUB_BASE_REF") or None,
        repository=repo,
    )


def build_environment(
    *,
    seed: int,
    root: Path,
    protocol_versions: Iterable[str] = (),
    labels: Mapping[str, str | None] | None = None,
    extra: Mapping[str, str] | None = None,
) -> Environment:
    labels = labels or {}
    return Environment(
        polygraph_version=__version__,
        python_version=platform.python_version(),
        platform=f"{sys.platform}-{platform.machine().lower() or 'unknown'}",
        ci=detect_ci(),
        git=git_info(root),
        framework=labels.get("framework"),
        framework_version=labels.get("framework_version"),
        config_label=labels.get("config_label"),
        model=labels.get("model"),
        protocol_versions=sorted(set(protocol_versions)),
        seed=seed,
        extra=dict(extra or {}),
    )


def build_results(
    *,
    runs: Sequence[Run],
    tools: Sequence[ToolInfo],
    coverage: Coverage,
    claims: Sequence[ClaimCheck],
    environment: Environment,
    include_content: bool,
) -> ResultsFile:
    has_text = any(
        p.text is not None
        for r in runs
        for p in ([r.final_answer] if r.final_answer else [])
        + [e.asked for e in r.ledger]
        + [e.told for e in r.ledger if e.told]
        + [e.happened.server_result for e in r.ledger if e.happened.server_result]
    )
    results = ResultsFile(
        generated_at=datetime.now(UTC),
        content_included=has_text,
        environment=environment,
        tools=list(tools),
        coverage=coverage,
        runs=list(runs),
        server_claims=list(claims),
        summary=summarize(runs, coverage),
    )
    if not include_content:
        results = results.stripped()
    else:
        results.content_included = any(p.text is not None for p in iter_payloads(results))
    return parse(results.to_json())  # round-trip: what we write is exactly what readers accept


def write_results(results: ResultsFile, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(results.to_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
