"""The ``polygraph`` pytest fixture object.

.. code-block:: python

    def test_pays_invoice(agent, polygraph, bank_db):
        answer = agent.run("Pay invoice INV-42")
        polygraph.check("paid once", bank_db.payments_for("INV-42") == 1)
        polygraph.assert_honest(answer)   # fails on lie, double action, hijack or loop

``polygraph.servers()`` hands out wrapped server configs (same shape as
``[tool.polygraph.servers]``): stdio commands now launch the proxy around the
real server, HTTP URLs point at a local proxy in front of the real one.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .faults import FaultPlan
from .judge import JudgeContext, run_judge
from .ledger import LedgerRecord, merge_ledgers
from .results import (
    BAD_OUTCOMES,
    MAX_LEDGER_ENTRIES_PER_RUN,
    FaultSpec,
    JudgeVerdict,
    LedgerEntry,
    Outcome,
    Payload,
    Run,
    StateCheck,
    TokenUsage,
)
from .scorer import ScoreInput, ScoreResult, score

if TYPE_CHECKING:  # pragma: no cover
    from .plugin import SessionState


class PolygraphFailure(AssertionError):
    """Raised by :meth:`Polygraph.assert_honest` for a bad outcome or a crash."""

    def __init__(self, outcome: Outcome, reasons: list[str]) -> None:
        self.outcome = outcome
        self.reasons = reasons
        lines = "\n".join(f"  - {r}" for r in reasons)
        super().__init__(f"Polygraph: {outcome.value.replace('_', ' ')}\n{lines}")


@dataclass(frozen=True)
class Variant:
    """One parametrisation of a test: a fault (or the control) and a repeat number."""

    fault: FaultSpec | None
    repeat: int = 0
    seed: int | None = None  # fixed seed (replay); otherwise derived

    @property
    def id(self) -> str:
        """The pytest parameter id, e.g. ``lost_response-bank.pay_invoice-r0``."""
        return f"{self.group_id}-r{self.repeat}"

    @property
    def group_id(self) -> str:
        """The id without the repeat number: the same for every repeat of one (test, fault)."""
        if self.fault is None:
            base = "control"
        else:
            f = self.fault
            base = f"{f.name.value}-{f.server}.{f.tool}"
            if f.on_call != 1:
                base += f"-call{f.on_call}"
            if f.params:
                digest = hashlib.sha256(
                    json.dumps(f.params, sort_keys=True, default=str).encode()
                ).hexdigest()[:6]
                base += f"-{digest}"
        return base


def derive_seed(base: int, nodeid: str) -> int:
    h = hashlib.sha256(f"{base}:{nodeid}".encode()).digest()
    return int.from_bytes(h[:4], "big")


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:20]


class Polygraph:
    """Per-test handle. Created by the plugin; tests get it as the ``polygraph`` fixture."""

    def __init__(
        self,
        state: SessionState,
        *,
        nodeid: str,
        variant: Variant,
        run_dir: Path,
        task: str | None = None,
        worlds: list[str] | None = None,
    ) -> None:
        self._state = state
        self.nodeid = nodeid
        self.variant = variant
        self.run_id = new_run_id()
        self.seed = variant.seed if variant.seed is not None else derive_seed(state.seed, nodeid)
        self.repeat = variant.repeat
        self.task = task
        self.worlds = worlds
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = datetime.now(UTC)
        self._t0 = time.monotonic()
        self._answer: str | None = None
        self._tokens: TokenUsage | None = None
        self._checks: list[StateCheck] = []
        self._servers: dict[str, dict[str, Any]] | None = None
        self._http: list[Any] = []
        self._ledgers: dict[str, Path] = {}
        self._finished: Run | None = None
        self._last_verdict: JudgeVerdict | None = None
        from .otel import new_span_id, new_trace_id

        self.trace_id = new_trace_id()
        self.span_id = new_span_id()

    @property
    def test_id(self) -> str:
        """The pytest node id without the repeat number, so repeats group together (pass^k)."""
        return self.nodeid.replace(self.variant.id, self.variant.group_id, 1)

    # ------------------------------------------------------------------ API
    @property
    def fault(self) -> FaultSpec | None:
        """The fault active for this test item (``None`` for the control run)."""
        return self.variant.fault

    def servers(self) -> dict[str, dict[str, Any]]:
        """Wrapped server configs, same shape as ``[tool.polygraph.servers]``."""
        if self._servers is None:
            self._servers = {name: self._wrap(name) for name in self._state.config.servers}
        return {k: dict(v) for k, v in self._servers.items()}

    def server(self, name: str) -> dict[str, Any]:
        servers = self.servers()
        if name not in servers:
            raise KeyError(f"no server {name!r} in [tool.polygraph.servers]")
        return servers[name]

    @property
    def records(self) -> list[LedgerRecord]:
        return merge_ledgers(self._ledgers.values())

    @property
    def ledger(self) -> list[LedgerEntry]:
        """The live ledger for the current run (asked · happened · told)."""
        return [r.entry for r in self.records]

    def record_answer(self, answer: str) -> None:
        self._answer = answer if answer is None else str(answer)

    def record_tokens(self, input: int, output: int) -> None:  # noqa: A002 - public API name
        prev = self._tokens or TokenUsage()
        self._tokens = TokenUsage(input=prev.input + int(input), output=prev.output + int(output))

    def check(self, name: str, passed: bool, detail: str | None = None) -> None:
        """Record an explicit state check (ground truth for "is the task done?")."""
        self._checks.append(StateCheck(name=name, passed=bool(passed), detail=detail))

    def score(self, answer: str | None = None) -> Outcome:
        """Score the run now. Never raises."""
        if answer is not None:
            self.record_answer(answer)
        return self._score().outcome

    def assert_honest(self, answer: str | None = None) -> Outcome:
        """Score now; raise :class:`PolygraphFailure` on a bad outcome or a crash."""
        if answer is not None:
            self.record_answer(answer)
        res = self._score()
        if res.outcome in BAD_OUTCOMES or res.outcome is Outcome.CRASH:
            raise PolygraphFailure(res.outcome, res.reasons)
        return res.outcome

    # ------------------------------------------------------------ plumbing
    def _plan_for(self, server: str) -> FaultPlan:
        cfg = self._state.config
        faults = (
            [self.fault] if self.fault is not None and self.fault.server in (server, "*") else []
        )
        return FaultPlan(
            run_id=self.run_id,
            server=server,
            seed=self.seed,
            faults=faults,
            timeout_s=cfg.timeout_s,
            slow_ms=cfg.slow_ms,
            fault_params=cfg.faults,
            traceparent=f"00-{self.trace_id}-{self.span_id}-01",
            replay_cassette=self._state.replay_cassette,
        )

    def _wrap(self, name: str) -> dict[str, Any]:
        cfg = self._state.config.servers[name]
        plan = self._plan_for(name)
        ledger = self.run_dir / f"ledger-{name}.jsonl"
        self._ledgers[name] = ledger
        if cfg.command is not None:
            plan_path = self.run_dir / f"plan-{name}.json"
            plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            command = [
                sys.executable,
                "-m",
                "polygraph",
                "proxy",
                "--fault-plan",
                str(plan_path),
                "--ledger",
                str(ledger),
                "--server-name",
                name,
                "--",
                *cfg.command,
            ]
            env = {
                **cfg.env,
                "POLYGRAPH_RUN_DIR": str(self.run_dir),
                "POLYGRAPH_RUN_ID": self.run_id,
            }
            out: dict[str, Any] = {"command": command, "env": env}
            cwd = cfg.cwd or str(self._state.config.root)
            out["cwd"] = cwd
            return out
        from .proxy.http import HttpProxy, ServerThread

        assert cfg.url is not None
        proxy = HttpProxy(plan, str(ledger), cfg.url, server_name=name)
        thread = ServerThread(proxy).start()
        self._http.append(thread)
        wrapped: dict[str, Any] = {"url": thread.url}
        if cfg.headers:
            wrapped["headers"] = dict(cfg.headers)
        return wrapped

    def _stop_http(self) -> None:
        for t in self._http:
            with contextlib.suppress(Exception):  # pragma: no cover
                t.stop()
        self._http.clear()

    def _score(
        self, *, error: str | None = None, extra_checks: list[StateCheck] | None = None
    ) -> ScoreResult:
        records = self.records
        plan = self._state.plan
        tools = {t.key: t.info for t in plan.tools} if plan is not None else {}
        claims = {t.key: t.claims for t in plan.tools} if plan is not None else {}
        checks = list(self._checks) + list(extra_checks or [])
        verdict = run_judge(
            self._answer,
            self._state.judge,
            JudgeContext(task=self.task, fault=self.fault, ledger=[r.entry for r in records]),
        )
        self._last_verdict = verdict
        return score(
            ScoreInput(
                records=records,
                fault=self.fault,
                answer=self._answer,
                checks=checks,
                verdict=verdict,
                error=error,
                tools=tools,
                claims=claims,
                loop_threshold=self._state.config.loop_threshold,
                run_id=self.run_id,
            )
        )

    def _finish(self, excinfo: BaseException | None = None) -> tuple[Run, ScoreResult]:
        """Build the final :class:`Run` (called by the plugin at teardown)."""
        self._stop_http()
        error = None
        extra: list[StateCheck] = []
        if excinfo is not None and not isinstance(excinfo, PolygraphFailure):
            if isinstance(excinfo, AssertionError):
                first = str(excinfo).strip().splitlines()[0] if str(excinfo).strip() else ""
                extra.append(
                    StateCheck(name="test assertion", passed=False, detail=first[:200] or None)
                )
            else:
                msg = str(excinfo).strip().splitlines()[0] if str(excinfo).strip() else ""
                error = f"{type(excinfo).__name__}: {msg}"[:500]
        res = self._score(error=error, extra_checks=extra)
        records = self.records
        versions = Counter(r.entry.protocol_version for r in records if r.entry.protocol_version)
        entries = [r.entry for r in records][:MAX_LEDGER_ENTRIES_PER_RUN]
        run = Run(
            id=self.run_id,
            test_id=self.test_id,
            task=self.task,
            fault=self.fault,
            seed=self.seed,
            repeat=self.repeat,
            started_at=self.started_at,
            duration_ms=max(0.0, (time.monotonic() - self._t0) * 1000.0),
            outcome=res.outcome,
            reasons=res.reasons,
            judge=self._last_verdict,
            final_answer=Payload.of(self._answer) if self._answer is not None else None,
            state_checks=list(self._checks) + extra,
            ledger=entries,
            tokens=self._tokens,
            error=error,
            protocol_version=versions.most_common(1)[0][0] if versions else None,
        )
        self._finished = run
        return run, res


def env_flag(name: str, env: Mapping[str, str] | None = None) -> bool:
    v = (env or os.environ).get(name, "")
    return v.lower() in {"1", "true", "yes", "on"}
