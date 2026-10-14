"""The ``polygraph`` command.

::

    polygraph plan [--config pyproject.toml] [--json] [--refresh]
    polygraph proxy --fault-plan PLAN.json --ledger OUT.jsonl -- <server command...>
    polygraph proxy --fault-plan PLAN.json --ledger OUT.jsonl --listen 127.0.0.1:0 --upstream URL
    polygraph replay <run-id> [--times N]
    polygraph report [--results ...] --html out.html | --junit out.xml | --pr-comment out.md
                     [--compare base.json]
    polygraph badge [--results ...] --out badge.svg
    polygraph upload [results.json] [--api URL] [--include-content] [--wait] [--compare-to BRANCH]
                     [--pr-comment OUT.md]
    polygraph validate results.json
    polygraph schema

Heavy imports happen inside each command so ``polygraph proxy`` starts fast.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__

DEFAULT_RESULTS = ".polygraph/results.json"


def _err(msg: str) -> None:
    print(f"polygraph: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #


def cmd_plan(ns: argparse.Namespace) -> int:
    from .config import ConfigError, load_config
    from .faults import DESCRIPTIONS
    from .planner import discover
    from .results import Priority

    try:
        config = load_config(ns.config)
    except ConfigError as exc:
        _err(str(exc))
        return 2
    if not config.servers:
        _err("no servers in [tool.polygraph.servers]")
        return 2
    plan = discover(config, refresh=ns.refresh)
    pairs = plan.pairs()
    if ns.json:
        out = {
            "tools": [t.info.model_dump(mode="json") for t in plan.tools],
            "claims": {t.key: sorted(t.claims) for t in plan.tools if t.claims},
            "pairs": [
                {"server": t.server, "tool": t.name, "fault": f.value, "priority": p.value}
                for t, f, p in pairs
            ],
            "errors": plan.errors,
        }
        print(json.dumps(out, indent=2))
        return 0 if not plan.errors else 1
    for t in plan.tools:
        h = t.info.hints
        print(f"{t.key}")
        for label in ("read_only", "destructive", "idempotent", "open_world"):
            hv = getattr(h, label)
            print(f"    {label:<12} {str(hv.value).lower():<6} ({hv.source.value})")
        if t.claims:
            print(f"    claims to check: {', '.join(sorted(t.claims))}")
        faults = ", ".join(f.value + (" *" if p is Priority.TOP else "") for f, p in t.faults)
        print(f"    faults: {faults}")
    print()
    print(f"{len(pairs)} applicable tool × fault pairs (* = top priority). Riskiest first:")
    for t, f, p in pairs[:10]:
        star = " (top priority)" if p is Priority.TOP else ""
        print(f"  {t.key} × {f.value}{star} — {DESCRIPTIONS[f]}")
    for server, err in plan.errors.items():
        _err(f"could not list tools of {server!r}: {err}")
    return 0 if not plan.errors else 1


# --------------------------------------------------------------------------- #
# proxy
# --------------------------------------------------------------------------- #


def _load_plan(source: str | None) -> Any:
    from .faults import FaultPlan

    source = source or os.environ.get("POLYGRAPH_FAULT_PLAN")
    if not source:
        return FaultPlan()
    if source.lstrip().startswith("{"):
        return FaultPlan.model_validate_json(source)
    return FaultPlan.model_validate_json(Path(source).read_text(encoding="utf-8"))


def cmd_proxy(ns: argparse.Namespace, server_cmd: list[str]) -> int:
    try:
        plan = _load_plan(ns.fault_plan)
    except (OSError, ValueError) as exc:
        _err(f"bad fault plan: {exc}")
        return 2
    ledger = ns.ledger or os.environ.get("POLYGRAPH_LEDGER")
    name = ns.server_name or plan.server
    if ns.listen:
        if not ns.upstream:
            _err("--listen needs --upstream URL")
            return 2
        return _proxy_http(plan, ledger, name, ns.listen, ns.upstream)
    if not server_cmd:
        _err("give the server command after '--', or use --listen/--upstream for HTTP")
        return 2
    import anyio

    from .config import resolve_command
    from .proxy.stdio import run_stdio_proxy

    server_cmd = resolve_command(server_cmd)

    env = dict(os.environ)
    # Make a bare "python" in the server command mean this interpreter's environment.
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    try:
        code = anyio.run(
            lambda: run_stdio_proxy(plan, ledger, server_cmd, env=env, server_name=name)
        )
    except FileNotFoundError as exc:
        _err(f"could not start the server: {exc}")
        code = 127
    sys.stdout.flush()
    sys.stderr.flush()
    # A worker thread may still be blocked reading stdin; don't wait for it.
    os._exit(code)


def _proxy_http(plan: Any, ledger: str | None, name: str, listen: str, upstream: str) -> int:
    import uvicorn

    from .proxy.http import HttpProxy, bind_socket

    host, _, port = listen.rpartition(":")
    sock = bind_socket(host or "127.0.0.1", int(port or 0))
    h, p = sock.getsockname()[:2]
    app = HttpProxy(plan, ledger, upstream, server_name=name)
    print(f"polygraph proxy listening on http://{h}:{p}/mcp -> {upstream}", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    server.run(sockets=[sock])
    return 0


# --------------------------------------------------------------------------- #
# replay / report / badge / validate / schema / upload
# --------------------------------------------------------------------------- #


def cmd_replay(ns: argparse.Namespace) -> int:
    from .replay import replay

    return replay(ns.run_id, root=Path(ns.root) if ns.root else None, times=ns.times)


def _read_results(path: str) -> Any:
    from .results import parse

    return parse(Path(path).read_text(encoding="utf-8"))


def cmd_report(ns: argparse.Namespace) -> int:
    results = _read_results(ns.results)
    if not (ns.html or ns.junit or ns.pr_comment):
        _err("choose at least one of --html, --junit, --pr-comment")
        return 2
    if ns.html:
        from .report import write_html

        print(f"wrote {write_html(results, Path(ns.html))}")
    if ns.junit:
        from .report import write_junit

        print(f"wrote {write_junit(results, Path(ns.junit))}")
    if ns.pr_comment:
        from .report.markdown import render_pr_comment

        base = compare = None
        if ns.compare:
            data = json.loads(Path(ns.compare).read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema") == "polygraph-results/v1":
                from .results import parse

                base = parse(data)
            elif isinstance(data, dict) and "head" in data and "base" in data:
                compare = data
            else:
                _err("--compare must be a results file or a cloud Compare JSON")
                return 2
        md = render_pr_comment(
            results, base=base, compare=compare, base_label=ns.base_label or "main"
        )
        out = Path(ns.pr_comment)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"wrote {out}")
    return 0


def cmd_badge(ns: argparse.Namespace) -> int:
    from .report import write_badge

    print(f"wrote {write_badge(_read_results(ns.results), Path(ns.out))}")
    return 0


def cmd_validate(ns: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from .results import parse

    try:
        results = parse(Path(ns.path).read_text(encoding="utf-8"))
    except OSError as exc:
        _err(str(exc))
        return 2
    except ValidationError as exc:
        _err(f"{ns.path} is not a valid polygraph-results/v1 file:")
        for e in exc.errors(include_input=False)[:20]:
            loc = ".".join(str(x) for x in e["loc"])
            print(f"  {loc}: {e['msg']}", file=sys.stderr)
        return 1
    except ValueError as exc:
        _err(f"{ns.path} is not valid JSON: {type(exc).__name__}")
        return 1
    s = results.summary
    print(
        f"ok: {s.runs} runs ({s.faulted_runs} with a fault), "
        f"coverage {results.coverage.covered}/{results.coverage.applicable}, "
        f"content included: {str(results.content_included).lower()}"
    )
    return 0


def cmd_schema(ns: argparse.Namespace) -> int:
    from .results import json_schema

    print(json.dumps(json_schema(), indent=2))
    return 0


def cmd_upload(ns: argparse.Namespace) -> int:
    from .config import ConfigError, load_config
    from .upload import run_upload

    mask = None
    try:
        mask = load_config(ns.config).mask
    except ConfigError as exc:
        _err(str(exc))
        return 2
    return run_upload(
        ns.path,
        api=ns.api,
        include_content=ns.include_content,
        mask=mask,
        wait=ns.wait,
        compare_to=ns.compare_to,
        pr_comment=ns.pr_comment,
        audience=ns.audience,
    )


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="polygraph",
        description="Polygraph for MCP: break your agent's tools, catch it lying.",
    )
    p.add_argument("--version", action="version", version=f"polygraph {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("plan", help="print the tool × fault matrix and label sources")
    sp.add_argument("--config", default=None, help="pyproject.toml (default: nearest)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--refresh", action="store_true", help="ignore the tool-list cache")

    sp = sub.add_parser("proxy", help="run the fault proxy (stdio wrapper or HTTP)")
    sp.add_argument("--fault-plan", default=None, help="plan JSON file (or $POLYGRAPH_FAULT_PLAN)")
    sp.add_argument("--ledger", default=None, help="ledger JSONL output (or $POLYGRAPH_LEDGER)")
    sp.add_argument("--server-name", default=None)
    sp.add_argument("--listen", default=None, metavar="HOST:PORT")
    sp.add_argument("--upstream", default=None, metavar="URL")

    sp = sub.add_parser("replay", help="re-run a recorded run with the same fault and seed")
    sp.add_argument("run_id")
    sp.add_argument("--results", default=DEFAULT_RESULTS)
    sp.add_argument("--times", type=int, default=1)
    sp.add_argument("--root", default=None, help="project root (default: current directory)")

    sp = sub.add_parser("report", help="HTML report, JUnit XML or PR comment from a results file")
    sp.add_argument("--results", default=DEFAULT_RESULTS)
    sp.add_argument("--html", default=None)
    sp.add_argument("--junit", default=None)
    sp.add_argument("--pr-comment", default=None)
    sp.add_argument("--compare", default=None, help="base results file or cloud Compare JSON")
    sp.add_argument("--base-label", default=None, help="name of the base (default: main)")

    sp = sub.add_parser("badge", help="write a local badge SVG")
    sp.add_argument("--results", default=DEFAULT_RESULTS)
    sp.add_argument("--out", required=True)

    sp = sub.add_parser("upload", help="upload results to the hosted dashboard (opt-in)")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--api", default="https://polygraphmcp.dev/api")
    sp.add_argument("--include-content", action="store_true")
    sp.add_argument("--wait", action="store_true")
    sp.add_argument("--compare-to", default=None, metavar="BRANCH")
    sp.add_argument("--pr-comment", default=None, metavar="OUT.md")
    sp.add_argument("--audience", default=None, help="OIDC audience (default polygraphmcp.dev)")
    sp.add_argument("--config", default=None, help="pyproject.toml with the mask setting")

    sp = sub.add_parser("validate", help="check a results file against the schema")
    sp.add_argument("path")

    sub.add_parser("schema", help="print the results JSON Schema")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    server_cmd: list[str] = []
    if "--" in args:
        i = args.index("--")
        args, server_cmd = args[:i], args[i + 1 :]
    ns = build_parser().parse_args(args)
    handlers = {
        "plan": cmd_plan,
        "replay": cmd_replay,
        "report": cmd_report,
        "badge": cmd_badge,
        "validate": cmd_validate,
        "schema": cmd_schema,
        "upload": cmd_upload,
    }
    if ns.command == "proxy":
        return cmd_proxy(ns, server_cmd)
    return int(handlers[ns.command](ns))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
