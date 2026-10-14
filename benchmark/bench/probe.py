"""Which MCP protocol version does a framework speak? Find out without a model.

The probe starts the bank world behind the Polygraph proxy (no faults), lets the
framework's own MCP client list the tools and call ``get_invoice`` once, and then
reads the proxy's ledger, which records the protocol version of every message.

    python -m bench.probe                  # every installed framework
    python -m bench.probe langgraph        # one framework

This is free and offline, so it also serves as the adapter smoke test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from polygraph.faults import FaultPlan
from polygraph.ledger import read_ledger

from bench.models import SCRIPTED, ModelSpec
from bench.prompt import SYSTEM_PROMPT


@dataclass
class ProbeResult:
    framework: str
    framework_version: str | None
    protocol_versions: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    error: str | None = None


def proxied_bank(workdir: Path) -> tuple[dict[str, dict[str, object]], Path]:
    """A server config for the bank world behind a fault-free stdio proxy, and its ledger."""
    plan = workdir / "plan-bank.json"
    plan.write_text(FaultPlan(server="bank").model_dump_json(), encoding="utf-8")
    ledger = workdir / "ledger-bank.jsonl"
    command = [
        sys.executable,
        "-m",
        "polygraph",
        "proxy",
        "--fault-plan",
        str(plan),
        "--ledger",
        str(ledger),
        "--server-name",
        "bank",
        "--",
        sys.executable,
        "-m",
        "polygraph.worlds.bank",
        "--db",
        str(workdir / "bank.sqlite"),
        "--reset",
    ]
    return {"bank": {"command": command, "env": {}, "cwd": str(workdir)}}, ledger


def probe(
    framework: str, *, config_label: str = "default", timeout_s: float = 120.0
) -> ProbeResult:
    from adapters import SCRIPTED as SCRIPTED_FRAMEWORKS
    from adapters import adapter_class

    cls = adapter_class(framework)
    out = ProbeResult(framework=framework, framework_version=cls.version())
    if not cls.available():
        out.error = f'not installed (pip install -e ".[{cls.extra}]")'
        return out
    with tempfile.TemporaryDirectory(prefix="pg-probe-", ignore_cleanup_errors=True) as tmp:
        servers, ledger = proxied_bank(Path(tmp))
        model = (
            ModelSpec(SCRIPTED, SCRIPTED)
            if framework in SCRIPTED_FRAMEWORKS
            else ModelSpec("openai", "probe-no-model-call")
        )
        agent = cls(servers, model, SYSTEM_PROMPT, config_label)
        try:
            out.tools = asyncio.run(asyncio.wait_for(agent.probe(), timeout_s))
        except Exception as exc:  # report, never raise: this is a diagnostic
            out.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        records = read_ledger(ledger) if ledger.exists() else []
    versions = Counter(r.entry.protocol_version for r in records if r.entry.protocol_version)
    out.protocol_versions = sorted(versions)
    out.methods = [r.entry.method for r in records]
    return out


def main(argv: list[str] | None = None) -> int:
    from adapters import ADAPTERS, adapter_class

    p = argparse.ArgumentParser(prog="python -m bench.probe", description=__doc__.split("\n")[0])
    p.add_argument("frameworks", nargs="*", help="default: every installed adapter")
    p.add_argument("--config", default="default", help="config label (exercises recipe hooks)")
    p.add_argument("--json", action="store_true")
    ns = p.parse_args(argv)
    names = ns.frameworks or [f for f in ADAPTERS if adapter_class(f).available()]
    results = [probe(f, config_label=ns.config) for f in names]
    if ns.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            status = r.error or f"protocol {', '.join(r.protocol_versions) or '?'}"
            print(f"{r.framework:<18} {r.framework_version or '-':<10} {status}")
    return 0 if all(r.error is None for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
