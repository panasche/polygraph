"""``run.py`` / ``polygraph-bench``: run the benchmark matrix.

framework × config label × model, each cell a separate ``pytest tasks/
--polygraph-auto`` run with a pinned seed and N repeats, each writing one
results file with ``environment.framework``, ``framework_version``,
``config_label`` and ``model`` set. At the end it prints the cost of every cell
in tokens and ₹ (from ``prices.toml``, which you fill in).

    python run.py --dry-run                                   # scripted agents, offline, free
    python run.py --framework langgraph --model openai:<id> --runs 5
    python run.py --framework pydantic-ai --config default --config R2 --config R5
    python run.py ... --judge llm --second-opinion --upload

Each framework runs in its own virtualenv (``.venvs/<framework>`` is found
automatically, or pass ``--python-for langgraph=/path/to/python``), because the
frameworks need different MCP SDK majors.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bench import BENCH_ROOT, configs, costs, models
from bench.pins import IST

DRY_RUN_FAULT_TIMINGS = {"timeout_s": 1, "slow_ms": 300}
DRY_RUN_FAULT_PARAMS = {"rate_limit": {"retry_after_s": 0.2}}
DEFAULT_API = "https://polygraphmcp.dev/api"
LEADERBOARD_PROJECT = "polygraph-dev/benchmark"
OK_EXIT_CODES = {0, 1}  # 1 = some tests failed (an agent crashed): still a valid run
LABEL_ENVS = (
    "POLYGRAPH_FRAMEWORK",
    "POLYGRAPH_FRAMEWORK_VERSION",
    "POLYGRAPH_MODEL",
    "POLYGRAPH_CONFIG_LABEL",
)


# --------------------------------------------------------------------------- #
# config file for one cell
# --------------------------------------------------------------------------- #


def base_config(pyproject: Path = BENCH_ROOT / "pyproject.toml") -> dict[str, Any]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return dict(data.get("tool", {}).get("polygraph", {}))


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list | tuple):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, Mapping):
        return "{ " + ", ".join(f"{_key(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _key(k: str) -> str:
    return k if k.replace("_", "").replace("-", "").isalnum() else json.dumps(k)


def dump_polygraph_toml(table: Mapping[str, Any]) -> str:
    """``[tool.polygraph]`` and its sub-tables as TOML (only the shapes Polygraph uses)."""
    lines = [
        "# written by run.py for one benchmark cell; edit benchmark/pyproject.toml instead",
        "",
    ]
    lines.append("[tool.polygraph]")
    subtables: list[tuple[str, Mapping[str, Any]]] = []
    for k, v in table.items():
        if isinstance(v, Mapping) and k in ("servers", "overrides", "faults"):
            subtables.append((k, v))
        else:
            lines.append(f"{_key(k)} = {_toml_value(v)}")
    for name, sub in subtables:
        if name == "faults":
            for fault, params in sub.items():
                lines += ["", f"[tool.polygraph.faults.{_key(fault)}]"]
                lines += [f"{_key(k)} = {_toml_value(v)}" for k, v in params.items()]
        else:
            lines += ["", f"[tool.polygraph.{name}]"]
            lines += [f"{_key(k)} = {_toml_value(v)}" for k, v in sub.items()]
    return "\n".join(lines) + "\n"


def cell_config(
    base: Mapping[str, Any],
    *,
    framework: str,
    config_label: str,
    model: str,
    seed: int,
    judge: str,
    dry_run: bool,
) -> dict[str, Any]:
    table = json.loads(json.dumps(base))  # deep copy of plain data
    table.update(
        {
            "seed": seed,
            "judge": "judge.llm_judge:claimed_success" if judge == "llm" else "rules",
            "framework": framework,
            "config_label": config_label,
            "model": model,
        }
    )
    if dry_run:
        table.update(DRY_RUN_FAULT_TIMINGS)
        faults = table.setdefault("faults", {})
        for name, params in DRY_RUN_FAULT_PARAMS.items():
            faults.setdefault(name, {}).update(params)
    return table


# --------------------------------------------------------------------------- #
# the matrix
# --------------------------------------------------------------------------- #


@dataclass
class Cell:
    framework: str
    config_label: str
    model: str
    python: str
    dir: Path
    results: Path
    framework_version: str | None = None
    exit_code: int | None = None
    status: str = "pending"  # pending | ok | failed | skipped
    message: str | None = None
    command: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.framework}__{self.config_label}__{models.parse(self.model).slug}"


def find_python(framework: str, overrides: Mapping[str, str]) -> str:
    """The interpreter for a framework: ``--python-for``, then ``.venvs/<fw>``, then this one."""
    if framework in overrides:
        return overrides[framework]
    from adapters import SCRIPTED

    if framework not in SCRIPTED:
        venv = BENCH_ROOT / ".venvs" / framework
        for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
            if candidate.exists():
                return str(candidate)
    return sys.executable


def check_framework(python: str, framework: str) -> tuple[bool, str | None]:
    """Is the framework installed for that interpreter? (ok, version or reason)"""
    code = (
        "import sys\n"
        "from adapters import adapter_class\n"
        "c = adapter_class(sys.argv[1])\n"
        "print(c.version() or '')\n"
        "sys.exit(0 if c.available() else 3)\n"
    )
    try:
        out = subprocess.run(
            [python, "-c", code, framework],
            cwd=BENCH_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"cannot start {python}: {type(exc).__name__}"
    if out.returncode == 3:
        return False, f'not installed for {python} (pip install -e ".[{framework}]")'
    if out.returncode != 0:
        return False, (out.stderr.strip().splitlines() or ["unknown error"])[-1][:300]
    return True, out.stdout.strip() or None


def pytest_command(
    cell: Cell,
    cfg_path: Path,
    *,
    runs: int,
    seed: int,
    faults: str | None,
    tools: str | None,
    keyword: str | None,
    marker: str | None,
    html: bool,
) -> list[str]:
    cmd = [
        cell.python, "-m", "pytest", "tasks",
        "--rootdir", str(BENCH_ROOT),
        "--polygraph-auto",
        "--polygraph-config", str(cfg_path),
        "--polygraph-json", str(cell.results),
        "--polygraph-runs", str(runs),
        "--polygraph-seed", str(seed),
        "--polygraph-include-content",
        "-q", "-p", "no:cacheprovider",
    ]  # fmt: skip
    if html:
        cmd += ["--polygraph-report", str(cell.dir / "report.html")]
    if faults:
        cmd += ["--polygraph-fault", faults]
    if tools:
        cmd += ["--polygraph-tool", tools]
    if keyword:
        cmd += ["-k", keyword]
    if marker:
        cmd += ["-m", marker]
    return cmd


def cell_env(cell: Cell, *, dry_run: bool, call_timeout: float) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in LABEL_ENVS}
    env.update(
        {
            "POLYGRAPH_BENCH_FRAMEWORK": cell.framework,
            "POLYGRAPH_BENCH_CONFIG": cell.config_label,
            "POLYGRAPH_BENCH_MODEL": cell.model,
            "POLYGRAPH_BENCH_CALL_TIMEOUT": str(call_timeout),
            "POLYGRAPH_BENCH_JUDGE_LOG": str(cell.dir / "judge-usage.jsonl"),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if dry_run:
        env.pop("POLYGRAPH_BENCH_JUDGE_MODEL", None)
    return env


def run_cell(cell: Cell, cmd: Sequence[str], env: Mapping[str, str], *, quiet: bool) -> int:
    log = cell.dir / "pytest.log"
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            list(cmd),
            cwd=BENCH_ROOT,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            fh.write(line)
            if not quiet:
                sys.stdout.write("    " + line)
                sys.stdout.flush()
        return proc.wait()


def validate_results(cell: Cell) -> str | None:
    """Problems with a cell's results file (``None`` = fine)."""
    from polygraph.results import parse

    if not cell.results.exists():
        return "no results file was written"
    try:
        r = parse(cell.results.read_text(encoding="utf-8"))
    except ValueError as exc:
        return f"results file does not validate: {str(exc)[:200]}"
    e = r.environment
    missing = [
        k for k, v in {
            "framework": e.framework, "framework_version": e.framework_version,
            "config_label": e.config_label, "model": e.model,
        }.items() if not v
    ]  # fmt: skip
    if missing:
        return "results file is missing environment." + ", ".join(missing)
    if (
        e.framework != cell.framework
        or e.config_label != cell.config_label
        or e.model != cell.model
    ):
        return "results file labels do not match the cell"
    if not r.runs:
        return "results file has no runs"
    return None


def judge_rows(cells: Iterable[Cell], prices: Mapping[str, costs.Price]) -> list[costs.CostRow]:
    rows: dict[str, costs.CostRow] = {}
    for cell in cells:
        log = cell.dir / "judge-usage.jsonl"
        if not log.exists():
            continue
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            m = str(rec.get("model"))
            row = rows.setdefault(m, costs.CostRow(label=f"LLM judge · {m}", model=m))
            row.runs += 1
            row.runs_with_tokens += 1
            row.tokens_in += int(rec.get("tokens_in") or 0)
            row.tokens_out += int(rec.get("tokens_out") or 0)
    for row in rows.values():
        row.price = prices.get(row.model or "")
        if row.price is None:
            row.notes.append(f"{row.model} is not priced in prices.toml")
    return list(rows.values())


def upload(paths: Iterable[Path], api: str) -> list[tuple[Path, int]]:
    """``polygraph upload`` for each file (payload text is stripped by default)."""
    out = []
    for p in paths:
        cmd = [sys.executable, "-m", "polygraph", "upload", str(p), "--api", api]
        print(f"uploading {p.name} to {api} (project {LEADERBOARD_PROJECT})")
        out.append((p, subprocess.run(cmd, cwd=BENCH_ROOT, check=False).returncode))
    return out


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python run.py",
        description="Run the Polygraph benchmark matrix (framework × config × model).",
    )
    p.add_argument(
        "--framework",
        action="append",
        default=[],
        metavar="NAME",
        help="langgraph, openai-agents, pydantic-ai, smolagents (repeatable; default all four)",
    )
    p.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="LABEL",
        help="default, best-practice, R1..R7, R1+R2, all-recipes (repeatable; default 'default')",
    )
    p.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="PROVIDER:ID",
        help=f"repeatable; default ${models.MODEL_ENV}",
    )
    p.add_argument(
        "--runs", type=int, default=None, help="repeats per (task, fault); default from pyproject"
    )
    p.add_argument(
        "--seed", type=int, default=None, help="base seed; default from pyproject (pinned)"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="scripted agents instead of an LLM: offline, free, deterministic",
    )
    p.add_argument("--faults", default=None, help="only these faults, comma separated")
    p.add_argument("--tools", default=None, help="only these tools, comma separated")
    p.add_argument("-k", dest="keyword", default=None, help="pytest -k expression over the tasks")
    p.add_argument("-m", dest="marker", default=None, help="pytest -m expression (e.g. headline)")
    p.add_argument(
        "--judge",
        choices=["rules", "llm"],
        default="rules",
        help=f"claim judge: Polygraph's rules or the narrow LLM judge (${models.JUDGE_MODEL_ENV})",
    )
    p.add_argument(
        "--second-opinion", action="store_true", help="run agent-polygraph's verify() on each run"
    )
    p.add_argument("--out", default=None, help="output folder (default out/<timestamp>)")
    p.add_argument(
        "--python-for",
        action="append",
        default=[],
        metavar="FRAMEWORK=PYTHON",
        help="interpreter for a framework (default .venvs/<framework> if present)",
    )
    p.add_argument("--prices", default=str(BENCH_ROOT / "prices.toml"))
    p.add_argument(
        "--allow-empty-best-practice",
        action="store_true",
        help="run 'best-practice' even when no maintainer settings exist yet (same as default)",
    )
    p.add_argument("--no-html", action="store_true", help="skip the HTML report per cell")
    p.add_argument(
        "--upload",
        action="store_true",
        help=f"upload each results file to Polygraph Cloud ({LEADERBOARD_PROJECT}); "
        "needs POLYGRAPH_TOKEN or GitHub OIDC",
    )
    p.add_argument("--api", default=DEFAULT_API)
    p.add_argument(
        "--quiet",
        action="store_true",
        help="only print the summary (pytest output goes to the log)",
    )
    return p


def _show(cmd: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(cmd)) if os.name == "nt" else shlex.join(cmd)


def _fail(msg: str) -> int:
    print(f"run.py: {msg}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    from adapters import ADAPTERS, FRAMEWORKS, SCRIPTED, adapter_class

    ns = build_parser().parse_args(argv)
    base = base_config()
    seed = ns.seed if ns.seed is not None else int(base.get("seed") or 20261014)
    runs = ns.runs if ns.runs is not None else int(base.get("runs") or 1)
    if runs < 1:
        return _fail("--runs must be at least 1")

    if ns.dry_run:
        frameworks = ns.framework or list(SCRIPTED)
        if any(f not in SCRIPTED for f in frameworks):
            return _fail(f"--dry-run uses the scripted agents only: {', '.join(SCRIPTED)}")
        model_ids = [models.SCRIPTED]
        if ns.judge == "llm":
            return _fail("--dry-run is offline: use --judge rules")
    else:
        frameworks = ns.framework or list(FRAMEWORKS)
        raw_models = ns.model or (
            [os.environ[models.MODEL_ENV]] if os.environ.get(models.MODEL_ENV) else []
        )
        if not raw_models:
            return _fail(
                f"no model: pass --model provider:model-id or set {models.MODEL_ENV} "
                "(or use --dry-run)"
            )
        try:
            specs = [models.parse(m) for m in raw_models]
        except ValueError as exc:
            return _fail(str(exc))
        if any(s.is_scripted for s in specs):
            return _fail("model 'scripted' is for --dry-run")
        for s in specs:
            missing = models.missing_key(s)
            if missing:
                return _fail(
                    f"{s} needs {missing} in the environment (keys are never read from files)"
                )
        model_ids = [str(s) for s in specs]
        if ns.judge == "llm":
            try:
                jspec = models.from_env(var=models.JUDGE_MODEL_ENV)
            except ValueError as exc:
                return _fail(f"--judge llm: {exc}")
            if models.missing_key(jspec):
                return _fail(f"--judge llm needs {jspec.key_env} in the environment")
    unknown = [f for f in frameworks if f not in ADAPTERS]
    if unknown:
        return _fail(f"unknown framework(s): {', '.join(unknown)}; known: {', '.join(ADAPTERS)}")
    try:
        labels = list(dict.fromkeys(configs.resolve(c).label for c in (ns.config or ["default"])))
    except ValueError as exc:
        return _fail(str(exc))
    overrides: dict[str, str] = {}
    for item in ns.python_for:
        fw, sep, path = item.partition("=")
        if not sep:
            return _fail(f"--python-for needs FRAMEWORK=PYTHON, got {item!r}")
        overrides[fw] = path
    try:
        prices = costs.load_prices(Path(ns.prices))
    except costs.PriceError as exc:
        return _fail(str(exc))

    stamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
    out = Path(ns.out) if ns.out else BENCH_ROOT / "out" / (stamp + ("-dry" if ns.dry_run else ""))
    (out / "results").mkdir(parents=True, exist_ok=True)
    print(f"Polygraph benchmark · seed {seed} · runs {runs} · output {out}")

    cells: list[Cell] = []
    for fw in frameworks:
        python = find_python(fw, overrides)
        ok, info = check_framework(python, fw)
        for label in labels:
            for model in model_ids:
                cell_dir = out / "cells" / f"{fw}__{label}__{models.parse(model).slug}"
                cell = Cell(
                    fw,
                    label,
                    model,
                    python,
                    cell_dir,
                    out / "results" / f"{fw}__{label}__{models.parse(model).slug}.json",
                )
                cells.append(cell)
                if not ok:
                    cell.status, cell.message = "skipped", info
                    continue
                cell.framework_version = info
                if (
                    label == configs.BEST_PRACTICE
                    and not adapter_class(fw).BEST_PRACTICE
                    and not ns.allow_empty_best_practice
                ):
                    cell.status = "skipped"
                    cell.message = (
                        "no best-practice settings from the maintainers yet "
                        "(it would equal 'default')"
                    )

    for cell in cells:
        print(
            f"\n== {cell.framework} {cell.framework_version or ''} · "
            f"{cell.config_label} · {cell.model}"
        )
        if cell.status == "skipped":
            print(f"   skipped: {cell.message}")
            continue
        cell.dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cell.dir / "pyproject.toml"
        cfg_path.write_text(
            dump_polygraph_toml(
                cell_config(
                    base,
                    framework=cell.framework,
                    config_label=cell.config_label,
                    model=cell.model,
                    seed=seed,
                    judge=ns.judge,
                    dry_run=ns.dry_run,
                )
            ),
            encoding="utf-8",
        )
        cell.command = pytest_command(
            cell,
            cfg_path,
            runs=runs,
            seed=seed,
            faults=ns.faults,
            tools=ns.tools,
            keyword=ns.keyword,
            marker=ns.marker,
            html=not ns.no_html,
        )
        print("   " + _show(cell.command[1:]))
        env = cell_env(cell, dry_run=ns.dry_run, call_timeout=5.0 if ns.dry_run else 30.0)
        cell.exit_code = run_cell(cell, cell.command, env, quiet=ns.quiet)
        problem = validate_results(cell)
        if cell.exit_code not in OK_EXIT_CODES or problem:
            cell.status = "failed"
            cell.message = (
                problem or f"pytest exited with {cell.exit_code} (see {cell.dir / 'pytest.log'})"
            )
            print(f"   FAILED: {cell.message}")
            continue
        cell.status = "ok"
        if ns.second_opinion:
            subprocess.run(
                [cell.python, "-m", "second_opinion", str(cell.results)],
                cwd=BENCH_ROOT,
                check=False,
            )

    done = [c for c in cells if c.status == "ok"]
    rows = [costs.cost_of(_parse(c.results), prices, c.name) for c in done]
    rows += judge_rows(done, prices)
    print("\nCost per run (tokens and ₹, prices from prices.toml):")
    print(costs.table(rows) if rows else "(no finished cells)")
    (out / "costs.json").write_text(json.dumps(costs.as_json(rows), indent=2), encoding="utf-8")

    uploads: list[tuple[Path, int]] = []
    if ns.upload and done:
        uploads = upload([c.results for c in done], ns.api)

    manifest = {
        "schema": "polygraph-bench-run/v1",
        "seed": seed,
        "runs": runs,
        "dry_run": ns.dry_run,
        "judge": ns.judge,
        "filters": {"faults": ns.faults, "tools": ns.tools, "k": ns.keyword, "m": ns.marker},
        "cells": [
            {
                **{k: v for k, v in asdict(c).items() if k not in ("dir", "results", "command")},
                "results": str(c.results.relative_to(out)) if c.status == "ok" else None,
                "command": c.command[1:],
            }
            for c in cells
        ],
        "uploads": [{"file": p.name, "exit_code": code} for p, code in uploads],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )

    print("\nCells:")
    for c in cells:
        print(f"  {c.status:<8} {c.name}" + (f"  ({c.message})" if c.message else ""))
    failed = [c for c in cells if c.status == "failed"]
    bad_uploads = [p for p, code in uploads if code != 0]
    return 1 if failed or bad_uploads or not done else 0


def _parse(path: Path) -> Any:
    from polygraph.results import parse

    return parse(path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
