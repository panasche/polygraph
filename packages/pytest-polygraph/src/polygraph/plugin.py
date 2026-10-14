"""The pytest plugin (entry point ``pytest11: polygraph``).

What it does:

- adds the ``polygraph`` fixture, the world fixtures (``bank_db``, ``shop_db``,
  ``inbox_db``), the markers and the ``--polygraph-*`` options;
- with ``--polygraph-auto`` multiplies every test that uses ``polygraph`` across
  the planned tool × fault matrix (``pytest_generate_tests``), plus one control
  run with no fault, times ``runs`` repeats;
- scores every run, writes ``.polygraph/results.json`` (always a valid
  ``polygraph-results/v1`` file), optional HTML/JUnit, local cassettes and
  spans, and prints one summary line;
- works with pytest-xdist: workers send their runs to the controller, which
  writes the files.
"""

from __future__ import annotations

import contextlib
import json
import logging
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .config import ConfigError, PolygraphConfig, load_config
from .fixture import Polygraph, PolygraphFailure, Variant
from .ledger import LedgerRecord
from .results import ClaimCheck, FaultName, FaultSpec, Run, ToolInfo
from .scorer import ScoreResult, aggregate_claims
from .worlds.fixtures import bank_db, inbox_db, shop_db  # noqa: F401 - registered as fixtures

log = logging.getLogger("polygraph.plugin")
STATE_KEY = pytest.StashKey["SessionState"]()
CALL_EXC_KEY = pytest.StashKey[BaseException]()


# --------------------------------------------------------------------------- #
# Options and markers
# --------------------------------------------------------------------------- #


def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("polygraph", "Polygraph for MCP")
    g.addoption(
        "--polygraph-auto",
        action="store_true",
        default=False,
        help="multiply tests across the planned tool × fault matrix",
    )
    g.addoption(
        "--polygraph-runs",
        type=int,
        default=None,
        metavar="N",
        help="repeats per (test, fault); default from config, else 1",
    )
    g.addoption(
        "--polygraph-seed",
        type=int,
        default=None,
        metavar="N",
        help="base seed; default random (printed in the summary)",
    )
    g.addoption("--polygraph-report", default=None, metavar="PATH", help="write the HTML report")
    g.addoption(
        "--polygraph-json",
        default=None,
        metavar="PATH",
        help="results file; default .polygraph/results.json",
    )
    g.addoption("--polygraph-junit", default=None, metavar="PATH", help="write JUnit XML")
    g.addoption(
        "--polygraph-otel-content",
        choices=["on", "off"],
        default=None,
        help="include payload text in OTel spans (default on locally, off in CI)",
    )
    g.addoption(
        "--polygraph-include-content",
        action="store_true",
        default=False,
        help="keep payload text in the results file (local only; uploads strip it)",
    )
    g.addoption(
        "--polygraph-control",
        dest="polygraph_control",
        action="store_true",
        default=True,
        help="also run each test once with no fault (default)",
    )
    g.addoption(
        "--no-polygraph-control",
        dest="polygraph_control",
        action="store_false",
        help="do not add the no-fault control run",
    )
    g.addoption(
        "--polygraph-fault",
        default=None,
        metavar="NAME[,NAME]",
        help="restrict the matrix to these faults",
    )
    g.addoption(
        "--polygraph-tool",
        default=None,
        metavar="NAME[,NAME]",
        help="restrict the matrix to these tools",
    )
    g.addoption(
        "--polygraph-config",
        default=None,
        metavar="PATH",
        help="pyproject.toml to read [tool.polygraph] from",
    )
    g.addoption(
        "--polygraph-refresh-tools",
        action="store_true",
        default=False,
        help="ask the servers for their tools again instead of using the cache",
    )
    g.addoption(
        "--polygraph-keep-unused",
        action="store_true",
        default=False,
        help="run faults on tools the test's control run never called (skipped by default)",
    )
    g.addoption("--polygraph-replay", default=None, help=__import__("argparse").SUPPRESS)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "polygraph(fault, tool, server=None, on_call=1, **params): run this test under one fault",
    )
    config.addinivalue_line("markers", "polygraph_task(text): human task label for reports")
    config.addinivalue_line(
        "markers", "polygraph_world(*servers): servers this test touches (narrows the matrix)"
    )
    try:
        state = SessionState(config)
    except ConfigError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.stash[STATE_KEY] = state


def _state(config: pytest.Config) -> SessionState:
    return config.stash[STATE_KEY]


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #


class SessionState:
    def __init__(self, config: pytest.Config) -> None:
        self.pytest_config = config
        opt = config.option
        root = Path(str(config.rootpath))
        cfg_path = getattr(opt, "polygraph_config", None)
        self.config: PolygraphConfig = load_config(cfg_path, start=root)
        workerinput = getattr(config, "workerinput", None)
        self.is_worker = workerinput is not None
        seed = getattr(opt, "polygraph_seed", None)
        if seed is None:
            seed = self.config.seed
        if seed is None and workerinput is not None:
            seed = workerinput.get("polygraph_seed")
        self.seed: int = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.runs_n: int = getattr(opt, "polygraph_runs", None) or self.config.runs
        self.auto: bool = bool(getattr(opt, "polygraph_auto", False))
        self.control: bool = bool(getattr(opt, "polygraph_control", True))
        self.include_content: bool = bool(getattr(opt, "polygraph_include_content", False))
        otel = getattr(opt, "polygraph_otel_content", None)
        from .writer import detect_ci

        self.otel_content: bool = (otel == "on") if otel else detect_ci() == "local"
        self.fault_filter = _csv(getattr(opt, "polygraph_fault", None))
        self.tool_filter = _csv(getattr(opt, "polygraph_tool", None))
        if self.fault_filter:
            known = {f.value for f in FaultName}
            bad = [f for f in self.fault_filter if f not in known]
            if bad:
                raise ConfigError(f"--polygraph-fault: unknown fault(s): {', '.join(bad)}")
        self.refresh = bool(getattr(opt, "polygraph_refresh_tools", False))
        self.judge = self.config.judge
        self.root = self.config.root
        self.out_dir = self.root / ".polygraph"
        json_opt = getattr(opt, "polygraph_json", None)
        self.json_path = Path(json_opt) if json_opt else self.out_dir / "results.json"
        self.html_path = getattr(opt, "polygraph_report", None)
        self.junit_path = getattr(opt, "polygraph_junit", None)
        self.explicit = bool(self.auto or json_opt or self.html_path or self.junit_path)
        self.replay_cassette: str | None = getattr(opt, "polygraph_replay", None)
        self.replay_header: dict[str, Any] | None = None
        if self.replay_cassette:
            from .replay import read_cassette

            self.replay_header, _ = read_cassette(Path(self.replay_cassette))
            self.seed = int(self.replay_header.get("base_seed", self.seed))
            self.explicit = True
        self._plan: Any = None
        self.runs: list[Run] = []
        #: faulted runs whose fault never fired (the test never made that call); left out
        self.not_fired = 0
        self.keep_unused = bool(getattr(opt, "polygraph_keep_unused", False))
        #: tools each test's control run used: (test key) -> tool names (+ "<tools/list>")
        self.touched: dict[str, set[str]] = {}
        self.skipped_unused = 0
        self.claims: list[ClaimCheck] = []
        self.protocol_versions: set[str] = set()
        self.written: Path | None = None
        self.summary_line: str | None = None
        self._spans: Any = None

    # -- planning ------------------------------------------------------------
    @property
    def plan(self) -> Any:
        if self._plan is None and self.config.servers:
            from .planner import discover

            self._plan = discover(self.config, cache_dir=self.out_dir, refresh=self.refresh)
        return self._plan

    def resolve_server(self, tool: str) -> str:
        servers = list(self.config.servers)
        if len(servers) == 1:
            return servers[0]
        plan = self.plan
        found = plan.find(tool) if plan is not None else None
        if found is not None:
            return str(found.server)
        return "*"

    def variants_for(self, metafunc: pytest.Metafunc) -> list[Variant]:
        if self.replay_header is not None:
            h = self.replay_header
            fault = FaultSpec.model_validate(h["fault"]) if h.get("fault") else None
            return [Variant(fault=fault, repeat=int(h.get("repeat", 0)), seed=int(h["seed"]))]
        faults: list[FaultSpec] = []
        for m in metafunc.definition.iter_markers("polygraph"):
            kw = dict(m.kwargs)
            name = kw.pop("fault", None) or (m.args[0] if m.args else None)
            tool = kw.pop("tool", None) or (m.args[1] if len(m.args) > 1 else None)
            if name is None or tool is None:
                raise pytest.UsageError(
                    f"{metafunc.definition.nodeid}: @pytest.mark.polygraph needs fault= and tool="
                )
            server = kw.pop("server", None) or self.resolve_server(str(tool))
            on_call = int(kw.pop("on_call", 1))
            try:
                faults.append(
                    FaultSpec(
                        name=FaultName(name),
                        server=server,
                        tool=str(tool),
                        on_call=on_call,
                        params=kw,
                    )
                )
            except ValueError as exc:
                raise pytest.UsageError(f"{metafunc.definition.nodeid}: {exc}") from exc
        if not faults and self.auto and self.plan is not None:
            worlds: list[str] | None = None
            for m in metafunc.definition.iter_markers("polygraph_world"):
                worlds = (worlds or []) + [str(a) for a in m.args]
            faults = self.plan.matrix(
                servers=worlds, faults=self.fault_filter, tools=self.tool_filter
            )
        variants: list[Variant] = []
        if self.control or not faults:
            variants.append(Variant(fault=None, repeat=0))
        for f in faults:
            for rep in range(self.runs_n):
                variants.append(Variant(fault=f, repeat=rep))
        return variants

    # -- collecting runs -------------------------------------------------------
    def add_run(
        self, run: Run, res: ScoreResult, pg: Polygraph, records: list[LedgerRecord]
    ) -> None:
        if run.fault is not None and not any(e.fault is run.fault.name for e in run.ledger):
            # The test never made the call the fault targets: it is not a faulted run.
            # Leaving it out keeps the rates and the coverage honest.
            self.not_fired += 1
            return
        self.runs.append(run)
        self.claims.extend(res.claim_checks)
        self.protocol_versions.update(
            r.entry.protocol_version for r in records if r.entry.protocol_version
        )
        from .replay import cassette_path, write_cassette

        with contextlib.suppress(OSError):
            write_cassette(
                cassette_path(self.root, run.id),
                {
                    "run_id": run.id,
                    "test_id": pg.nodeid,  # the exact node id, for replay
                    "rootdir": str(self.root),
                    "fault": run.fault.model_dump(mode="json") if run.fault else None,
                    "seed": run.seed,
                    "base_seed": self.seed,
                    "repeat": run.repeat,
                    "task": run.task,
                    "outcome": run.outcome.value,
                },
                records,
            )
        try:
            if self._spans is None:
                from .otel import SpanSink

                self._spans = SpanSink(self.out_dir / "spans", include_content=self.otel_content)
            self._spans.export(run, records, trace_id=pg.trace_id, span_id=pg.span_id)
        except Exception as exc:  # pragma: no cover - tracing must never fail a test
            log.warning("could not export spans for run %s: %s", run.id, type(exc).__name__)

    def to_wire(self) -> str:
        return json.dumps(
            {
                "runs": [r.model_dump(mode="json") for r in self.runs],
                "claims": [c.model_dump(mode="json") for c in self.claims],
                "versions": sorted(self.protocol_versions),
                "not_fired": self.not_fired,
            }
        )

    def merge_wire(self, data: str) -> None:
        obj = json.loads(data)
        self.runs.extend(Run.model_validate(r) for r in obj.get("runs", []))
        self.claims.extend(ClaimCheck.model_validate(c) for c in obj.get("claims", []))
        self.protocol_versions.update(obj.get("versions", []))
        self.not_fired += int(obj.get("not_fired", 0))

    # -- output ----------------------------------------------------------------
    def write_outputs(self) -> None:
        if self._spans is not None:
            self._spans.shutdown()
        if not self.runs and not self.explicit:
            return
        from .metrics import summarize
        from .results import Coverage
        from .writer import build_environment, build_results, write_results

        plan = self.plan if (self.runs or self.auto) else None
        tools: list[ToolInfo] = plan.tool_infos if plan is not None else []
        coverage = (
            plan.coverage(self.runs, self.runs_n)
            if plan is not None
            else Coverage(applicable=0, covered=0)
        )
        results = build_results(
            runs=self.runs,
            tools=tools,
            coverage=coverage,
            claims=aggregate_claims(self.claims),
            environment=build_environment(
                seed=self.seed,
                root=self.root,
                protocol_versions=self.protocol_versions,
                labels=self.config.labels(),
                extra={"faulted_runs_not_fired": str(self.not_fired)} if self.not_fired else None,
            ),
            include_content=self.include_content,
        )
        self.written = write_results(results, self.json_path)
        from .report import write_html, write_junit

        if self.html_path:
            write_html(results, Path(self.html_path))
        if self.junit_path:
            write_junit(results, Path(self.junit_path))
        s = summarize(results.runs, results.coverage)
        self.summary_line = summary_line(
            s.silent_lie.numerator,
            s.silent_lie.denominator,
            s.double_action.numerator,
            s.double_action.denominator,
            s.hijack.numerator,
            s.hijack.denominator,
            results.coverage.covered,
            results.coverage.applicable,
        )


def summary_line(
    lie: int, lie_d: int, dbl: int, dbl_d: int, hij: int, hij_d: int, cov: int, cov_d: int
) -> str:
    """``Polygraph: silent-lie 0/40 · double-action 1/40 · hijack 0/20 · coverage 18/22``."""
    return (
        f"Polygraph: silent-lie {lie}/{lie_d} · double-action {dbl}/{dbl_d} · "
        f"hijack {hij}/{hij_d} · coverage {cov}/{cov_d}"
    )


def _printable(text: str, encoding: str | None) -> str:
    try:
        text.encode(encoding or "utf-8")
        return text
    except (UnicodeEncodeError, LookupError):
        return text.replace("·", "|").replace("×", "x")


# --------------------------------------------------------------------------- #
# Collection: multiply tests across the matrix
# --------------------------------------------------------------------------- #


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "polygraph" not in metafunc.fixturenames:
        return
    variants = _state(metafunc.config).variants_for(metafunc)
    metafunc.parametrize(
        "_polygraph_variant", variants, ids=[v.id for v in variants], indirect=True
    )


@pytest.fixture
def _polygraph_variant(request: pytest.FixtureRequest) -> Variant:
    return getattr(request, "param", None) or Variant(fault=None)


def _test_key(node: Any, variant: Variant) -> str:
    """The test with every parameter except Polygraph's own variant."""
    callspec = getattr(node, "callspec", None)
    cid = callspec.id if callspec is not None else ""
    other = "-".join(p for p in cid.replace(variant.id, "").split("-") if p)
    return f"{node.path}::{getattr(node, 'originalname', node.name)}[{other}]"


def _skip_if_unused(state: SessionState, node: Any, variant: Variant) -> None:
    fault = variant.fault
    if fault is None or state.keep_unused or state.replay_header is not None:
        return
    touched = state.touched.get(_test_key(node, variant))
    if touched is None:
        return  # no control run seen (yet): run it
    if fault.name is FaultName.DESCRIPTION_POISON:
        if "<tools/list>" not in touched:
            state.skipped_unused += 1
            pytest.skip(
                "Polygraph: this test never lists tools, so a poisoned description can't reach it"
            )
        return
    if fault.tool not in touched and fault.tool != "*":
        state.skipped_unused += 1
        pytest.skip(
            f"Polygraph: the control run never called {fault.tool}, so this fault can't fire"
        )


@pytest.fixture
def polygraph(
    request: pytest.FixtureRequest,
    _polygraph_variant: Variant,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Polygraph]:
    """Break this test's MCP tools on plan and score what the agent did."""
    state = _state(request.config)
    _skip_if_unused(state, request.node, _polygraph_variant)
    task_marker = request.node.get_closest_marker("polygraph_task")
    world_marker = request.node.get_closest_marker("polygraph_world")
    pg = Polygraph(
        state,
        nodeid=request.node.nodeid,
        variant=_polygraph_variant,
        run_dir=tmp_path_factory.mktemp("polygraph-run"),
        task=str(task_marker.args[0]) if task_marker and task_marker.args else None,
        worlds=[str(a) for a in world_marker.args] if world_marker else None,
    )
    yield pg
    exc = request.node.stash.get(CALL_EXC_KEY, None)
    if isinstance(exc, pytest.skip.Exception):
        pg._stop_http()
        return
    if isinstance(exc, pytest.xfail.Exception):
        exc = None  # an expected failure is the test's verdict, not a fact about the state
    elif isinstance(exc, pytest.fail.Exception):
        exc = AssertionError(str(exc))
    run, res = pg._finish(exc)
    records = pg.records
    if run.fault is None:
        used = {str(r.entry.tool) for r in records if r.entry.method == "tools/call"}
        if any(r.entry.method == "tools/list" for r in records):
            used.add("<tools/list>")
        state.touched[_test_key(request.node, _polygraph_variant)] = used
    state.add_run(run, res, pg, records)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    if call.when == "call" and call.excinfo is not None:
        item.stash[CALL_EXC_KEY] = call.excinfo.value
    return report


# --------------------------------------------------------------------------- #
# Session end, xdist and the summary line
# --------------------------------------------------------------------------- #


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash.get(STATE_KEY, None)
    if state is None:
        return
    if state.is_worker:
        session.config.workeroutput["polygraph"] = state.to_wire()  # type: ignore[attr-defined]
        if state._spans is not None:
            state._spans.shutdown()
        return
    state.write_outputs()


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    """xdist: give every worker the same base seed."""
    state = node.config.stash.get(STATE_KEY, None)
    if state is not None:
        node.workerinput["polygraph_seed"] = state.seed


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: Any) -> None:
    """xdist: collect a worker's runs."""
    state = node.config.stash.get(STATE_KEY, None)
    data = getattr(node, "workeroutput", {}).get("polygraph")
    if state is not None and data:
        state.merge_wire(data)


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    state = config.stash.get(STATE_KEY, None)
    if state is None or state.summary_line is None:
        return
    tr = terminalreporter
    encoding = getattr(getattr(getattr(tr, "_tw", None), "_file", None), "encoding", None)
    tr.write_sep("-", "polygraph")
    tr.write_line(_printable(state.summary_line, encoding))
    where = f"results {state.written}" if state.written else ""
    extra = f" · report {state.html_path}" if state.html_path else ""
    if state.skipped_unused:
        extra += f" · {state.skipped_unused} runs skipped (tool not used by the test)"
    if state.not_fired:
        extra += f" · {state.not_fired} faulted runs left out (the fault never fired)"
    tr.write_line(_printable(f"Polygraph seed {state.seed} · {where}{extra}", encoding))
    plan = state._plan
    if plan is not None and plan.errors:
        for server, err in plan.errors.items():
            tr.write_line(f"Polygraph could not list tools of {server!r}: {err}")


__all__ = ["Polygraph", "PolygraphFailure", "SessionState"]
