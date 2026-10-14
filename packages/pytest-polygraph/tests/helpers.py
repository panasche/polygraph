"""Shared helpers for the test suite: start worlds, wrap them in the proxy, run scripted agents."""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polygraph.config import PolygraphConfig
from polygraph.faults import FaultPlan
from polygraph.judge import run_judge
from polygraph.ledger import LedgerRecord, merge_ledgers
from polygraph.planner import Plan, build_plan
from polygraph.proxy.http import HttpProxy, ServerThread
from polygraph.results import FaultName, FaultSpec, StateCheck
from polygraph.scorer import ScoreInput, ScoreResult, score
from polygraph.worlds import db_class
from polygraph.worlds._mcp import McpServer

WORLD_SERVER = {"bank": "bank", "shop": "shop", "inbox": "inbox"}


def world_cmd(world: str, *extra: str) -> list[str]:
    return [sys.executable, "-m", f"polygraph.worlds.{world}", *extra]


def build_world(world: str, db_path: Path) -> tuple[McpServer, Any]:
    mod = __import__(f"polygraph.worlds.{world}", fromlist=["build"])
    db = db_class(world)(db_path)
    db.reset()
    return mod.build(db), db


def world_tools(world: str, tmp: Path) -> list[dict[str, Any]]:
    server, _ = build_world(world, tmp / f"_tools_{world}.sqlite")
    return [t.definition() for t in server.tools.values()]


def world_plan(world: str, tmp: Path, config: PolygraphConfig | None = None) -> Plan:
    return build_plan({world: world_tools(world, tmp)}, config)


@contextlib.contextmanager
def http_world(world: str, db_path: Path, *, sse: bool = False) -> Iterator[tuple[str, Any]]:
    """Serve a world over Streamable HTTP in a background thread. Yields (url, db)."""
    server, db = build_world(world, db_path)
    thread = ServerThread(server.asgi_app(sse=sse)).start()
    try:
        yield thread.url, db
    finally:
        thread.stop()


@contextlib.contextmanager
def http_proxy(plan: FaultPlan, ledger: Path, upstream: str) -> Iterator[str]:
    proxy = HttpProxy(plan, str(ledger), upstream, server_name=plan.server)
    thread = ServerThread(proxy).start()
    try:
        yield thread.url
    finally:
        thread.stop()


def fault(name: str, tool: str, server: str, on_call: int = 1, **params: Any) -> FaultSpec:
    return FaultSpec(name=FaultName(name), server=server, tool=tool, on_call=on_call, params=params)


@dataclass
class Scenario:
    answer: str
    result: ScoreResult
    records: list[LedgerRecord]
    db: Any
    agent: Any
    checks: list[StateCheck] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        return self.result.outcome.value

    def calls(self, tool: str | None = None) -> list[LedgerRecord]:
        return [
            r
            for r in self.records
            if r.entry.method == "tools/call" and (tool is None or r.entry.tool == tool)
        ]


Checks = Callable[[Any, str], Sequence[tuple[str, bool]]]


def run_scenario(
    tmp_path: Path,
    *,
    world: str,
    agent_cls: type,
    task: str,
    fault_spec: FaultSpec | None = None,
    transport: str = "stdio",
    era: str = "modern",
    sse: bool = False,
    checks: Checks | None = None,
    seed: int = 7,
    timeout_s: float = 1.0,
    slow_ms: int = 100,
    fault_params: dict[str, dict[str, Any]] | None = None,
    agent_kwargs: dict[str, Any] | None = None,
    listen: bool = False,
) -> Scenario:
    """Run one scripted agent against one world through the proxy and score it."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = FaultPlan(
        run_id="testrun01",
        server=world,
        seed=seed,
        faults=[fault_spec] if fault_spec else [],
        timeout_s=timeout_s,
        slow_ms=slow_ms,
        fault_params=fault_params or {"rate_limit": {"retry_after_s": 0.05}},
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )
    ledger = run_dir / f"ledger-{world}.jsonl"
    db_path = run_dir / f"{world}.sqlite"
    kwargs = {"era": era, "call_timeout": 15.0, **(agent_kwargs or {})}
    with contextlib.ExitStack() as stack:
        if transport == "stdio":
            db = db_class(world)(db_path)
            db.reset()
            plan_path = run_dir / "plan.json"
            plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
            cfg: dict[str, Any] = {
                "command": [
                    sys.executable,
                    "-m",
                    "polygraph",
                    "proxy",
                    "--fault-plan",
                    str(plan_path),
                    "--ledger",
                    str(ledger),
                    "--",
                    *world_cmd(world, "--db", str(db_path)),
                ],
            }
        else:
            url, db = stack.enter_context(http_world(world, db_path, sse=sse))
            proxy_url = stack.enter_context(http_proxy(plan, ledger, url))
            cfg = {"url": proxy_url}
        agent = agent_cls({world: cfg}, **kwargs)
        with agent:
            if listen:
                for c in agent.clients.values():
                    c.listen()
            answer = agent.run(task)
    records = merge_ledgers([ledger])
    state = [StateCheck(name=n, passed=bool(p)) for n, p in (checks(db, answer) if checks else [])]
    p = world_plan(world, tmp_path)
    result = score(
        ScoreInput(
            records=records,
            fault=fault_spec,
            answer=answer,
            checks=state,
            verdict=run_judge(answer),
            tools={t.key: t.info for t in p.tools},
            claims={t.key: t.claims for t in p.tools},
            run_id="testrun01",
        )
    )
    return Scenario(answer=answer, result=result, records=records, db=db, agent=agent, checks=state)


def dump(records: Sequence[LedgerRecord]) -> str:  # pragma: no cover - debugging aid
    return "\n".join(
        json.dumps(
            {
                "seq": r.entry.seq,
                "method": r.entry.method,
                "tool": r.entry.tool,
                "fault": r.entry.fault.value if r.entry.fault else None,
                "fwd": r.entry.happened.forwarded,
                "told_err": r.entry.told_is_error,
                "note": r.entry.happened.note,
            }
        )
        for r in records
    )
