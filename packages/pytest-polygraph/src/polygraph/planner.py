"""Auto-planning: tool labels → which faults to aim at which tools, and fault coverage.

Label priority for every hint (the report shows the source):

1. your override in ``[tool.polygraph.overrides]`` (``override``);
2. the server's MCP annotation (``server``);
3. a guess from the tool name (``guessed``);
4. the MCP spec default (``default``): ``readOnly=false``, ``destructive=true``,
   ``idempotent=false``, ``openWorld=true`` — it assumes the worst.

Labels are only used to decide *what to test*; the tests then check whether
the labels are true (see the claim checks in :mod:`polygraph.scorer`).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import PolygraphConfig, ServerConfig, resolve_command
from .results import (
    FAULT_FAMILY,
    Coverage,
    CoveragePair,
    FaultFamily,
    FaultName,
    FaultSpec,
    HintValue,
    LabelSource,
    Priority,
    Run,
    ToolHints,
    ToolInfo,
)

SPEC_DEFAULTS: dict[str, bool] = {
    "read_only": False,
    "destructive": True,
    "idempotent": False,
    "open_world": True,
}
ANNOTATION_KEYS: dict[str, str] = {
    "read_only": "readOnlyHint",
    "destructive": "destructiveHint",
    "idempotent": "idempotentHint",
    "open_world": "openWorldHint",
}

#: name prefixes that suggest "changes things" (the first five come from the research doc)
CHANGING_PREFIXES = (
    "pay_",
    "send_",
    "create_",
    "update_",
    "delete_",
    "place_",
    "cancel_",
    "remove_",
    "add_",
    "set_",
    "write_",
    "submit_",
    "transfer_",
    "book_",
)
#: name prefixes that suggest "read-only"
READ_PREFIXES = (
    "get_",
    "list_",
    "search_",
    "read_",
    "find_",
    "fetch_",
    "lookup_",
    "show_",
    "describe_",
    "view_",
)
DESTRUCTIVE_PREFIXES = ("delete_", "remove_", "cancel_")

UNAVAILABLE = (
    FaultName.TIMEOUT,
    FaultName.SLOW,
    FaultName.ERROR,
    FaultName.RATE_LIMIT,
    FaultName.AUTH_EXPIRED,
)
FAMILY_RISK = {
    FaultFamily.DIVERGENT: 0,
    FaultFamily.HOSTILE: 1,
    FaultFamily.CORRUPT: 2,
    FaultFamily.UNAVAILABLE: 3,
}
FAULT_ORDER = {f: i for i, f in enumerate(FaultName)}


def guess_from_name(name: str) -> dict[str, bool]:
    """Hints guessed from a tool name, e.g. ``pay_`` → changes things."""
    lower = name.lower()
    if lower.startswith(READ_PREFIXES):
        return {"read_only": True, "destructive": False, "idempotent": True}
    if lower.startswith(CHANGING_PREFIXES):
        out = {"read_only": False}
        if lower.startswith(DESTRUCTIVE_PREFIXES):
            out["destructive"] = True
        return out
    return {}


def resolve_hints(
    server: str, tool: Mapping[str, Any], config: PolygraphConfig | None = None
) -> ToolHints:
    name = str(tool.get("name", ""))
    raw_ann = tool.get("annotations")
    annotations: dict[str, Any] = raw_ann if isinstance(raw_ann, dict) else {}
    override = config.override_for(server, name) if config is not None else None
    guess = guess_from_name(name)
    values: dict[str, HintValue] = {}
    for hint, key in ANNOTATION_KEYS.items():
        ov = getattr(override, hint) if override is not None else None
        if ov is not None:
            values[hint] = HintValue(value=ov, source=LabelSource.OVERRIDE)
        elif isinstance(annotations.get(key), bool):
            values[hint] = HintValue(value=annotations[key], source=LabelSource.SERVER)
        elif hint in guess:
            values[hint] = HintValue(value=guess[hint], source=LabelSource.GUESSED)
        else:
            values[hint] = HintValue(value=SPEC_DEFAULTS[hint], source=LabelSource.DEFAULT)
    return ToolHints(**values)


def applicable_faults(hints: ToolHints) -> list[tuple[FaultName, Priority]]:
    """The research doc's table (§5.3): which faults make sense for this kind of tool."""
    out: dict[FaultName, Priority] = {}
    for f in UNAVAILABLE:
        out[f] = Priority.NORMAL
    if hints.read_only.value:
        for f in (
            FaultName.PARTIAL,
            FaultName.MALFORMED,
            FaultName.SCHEMA_DRIFT,
            FaultName.STALE,
            FaultName.INJECTION,
        ):
            out[f] = Priority.NORMAL
    else:
        out[FaultName.MALFORMED] = Priority.NORMAL
        out[FaultName.LOST_RESPONSE] = Priority.TOP
        out[FaultName.DUPLICATE_DELIVERY] = Priority.TOP
    if hints.open_world.value:
        out[FaultName.INJECTION] = Priority.TOP
    out[FaultName.DESCRIPTION_POISON] = Priority.NORMAL
    return sorted(out.items(), key=lambda kv: FAULT_ORDER[kv[0]])


def server_claims(tool: Mapping[str, Any]) -> set[str]:
    """What the *server* claims about a tool that Polygraph can check."""
    raw_ann = tool.get("annotations")
    ann: dict[str, Any] = raw_ann if isinstance(raw_ann, dict) else {}
    claims: set[str] = set()
    if ann.get("idempotentHint") is True and ann.get("readOnlyHint") is not True:
        claims.add("idempotent")
    return claims


def description_hash(tool: Mapping[str, Any]) -> str:
    return hashlib.sha256(str(tool.get("description") or "").encode("utf-8")).hexdigest()


@dataclass
class ToolPlan:
    server: str
    definition: dict[str, Any]
    info: ToolInfo
    faults: list[tuple[FaultName, Priority]]
    claims: set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def key(self) -> str:
        return self.info.key


def risk_key(tool: ToolPlan, fault: FaultName, priority: Priority) -> tuple[Any, ...]:
    h = tool.info.hints
    return (
        0 if priority is Priority.TOP else 1,
        FAMILY_RISK[FAULT_FAMILY[fault]],
        0 if not h.read_only.value else 1,
        0 if h.destructive.value else 1,
        0 if not h.idempotent.value else 1,
        tool.server,
        tool.name,
        FAULT_ORDER[fault],
    )


@dataclass
class Plan:
    tools: list[ToolPlan] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def tool_infos(self) -> list[ToolInfo]:
        return [t.info for t in self.tools]

    def find(self, tool: str, server: str | None = None) -> ToolPlan | None:
        for t in self.tools:
            if t.name == tool and (server is None or t.server == server):
                return t
        return None

    def pairs(self) -> list[tuple[ToolPlan, FaultName, Priority]]:
        """Every applicable (tool, fault) pair, riskiest first."""
        out = [(t, f, p) for t in self.tools for f, p in t.faults]
        out.sort(key=lambda x: risk_key(*x))
        return out

    def matrix(
        self,
        *,
        servers: Iterable[str] | None = None,
        faults: Iterable[str] | None = None,
        tools: Iterable[str] | None = None,
        params: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[FaultSpec]:
        """Fault specs for ``--polygraph-auto``, optionally narrowed."""
        s = set(servers) if servers is not None else None
        f = set(faults) if faults is not None else None
        tl = set(tools) if tools is not None else None
        out = []
        for tool, fault, _prio in self.pairs():
            if s is not None and tool.server not in s:
                continue
            if f is not None and fault.value not in f:
                continue
            if tl is not None and tool.name not in tl and tool.key not in tl:
                continue
            out.append(
                FaultSpec(
                    name=fault,
                    server=tool.server,
                    tool=tool.name,
                    params=dict((params or {}).get(fault.value, {})),
                )
            )
        return out

    def coverage(self, runs: Iterable[Run], runs_required: int) -> Coverage:
        done: dict[tuple[str, str, FaultName], int] = {}
        for r in runs:
            if r.fault is not None:
                k = (r.fault.server, r.fault.tool, r.fault.name)
                done[k] = done.get(k, 0) + 1
        pairs = [
            CoveragePair(
                server=t.server,
                tool=t.name,
                fault=f,
                priority=p,
                runs_required=runs_required,
                runs_done=done.get((t.server, t.name, f), 0),
            )
            for t, f, p in self.pairs()
        ]
        return Coverage(
            applicable=len(pairs), covered=sum(1 for p in pairs if p.covered), pairs=pairs
        )


def build_plan(
    tools_by_server: Mapping[str, list[dict[str, Any]]],
    config: PolygraphConfig | None = None,
    errors: Mapping[str, str] | None = None,
) -> Plan:
    plan = Plan(errors=dict(errors or {}))
    for server, tools in tools_by_server.items():
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            hints = resolve_hints(server, tool, config)
            info = ToolInfo(
                server=server,
                name=tool["name"],
                hints=hints,
                description_sha256=description_hash(tool),
                has_output_schema=isinstance(tool.get("outputSchema"), dict),
            )
            plan.tools.append(
                ToolPlan(server, dict(tool), info, applicable_faults(hints), server_claims(tool))
            )
    return plan


# --------------------------------------------------------------------------- #
# Asking the servers (with a cache in .polygraph/)
# --------------------------------------------------------------------------- #

CACHE_FILE = "tools-cache.json"
CACHE_MAX_AGE = timedelta(days=1)


def _config_key(cfg: ServerConfig) -> str:
    return hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()


def fetch_tools(
    cfg: ServerConfig, *, timeout: float = 30.0, root: Path | None = None
) -> list[dict[str, Any]]:
    """Ask one server for its tools (no proxy, no faults)."""
    from .testing.client import McpClient

    conn: dict[str, Any]
    if cfg.command is not None:
        run_dir = tempfile.mkdtemp(prefix="polygraph-discover-")
        env = {**cfg.env, "POLYGRAPH_RUN_DIR": run_dir}
        exe_dir = os.path.dirname(sys.executable)
        env["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")
        cwd = cfg.cwd or (str(root) if root else None)
        conn = {"command": resolve_command(cfg.command), "env": env, "cwd": cwd}
    else:
        conn = {"url": cfg.url, "headers": cfg.headers}
    with McpClient.connect(conn, timeout=timeout, client_name="polygraph-planner") as client:
        return client.list_tools(refresh=True)


def discover(
    config: PolygraphConfig,
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
    servers: Iterable[str] | None = None,
) -> Plan:
    """Build the plan from every configured server, using and updating the cache."""
    cache_dir = cache_dir or (config.root / ".polygraph")
    cache_path = cache_dir / CACHE_FILE
    cache: dict[str, Any] = {}
    if cache_path.exists() and not refresh:
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            cache = raw.get("servers", {}) if isinstance(raw, dict) else {}
        except (ValueError, OSError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
    wanted = set(servers) if servers is not None else None
    tools: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    changed = False
    now = datetime.now(UTC)
    for name, cfg in config.servers.items():
        if wanted is not None and name not in wanted:
            continue
        key = _config_key(cfg)
        hit = cache.get(name)
        if isinstance(hit, dict) and hit.get("key") == key:
            try:
                fresh = now - datetime.fromisoformat(hit["fetched_at"]) < CACHE_MAX_AGE
            except (KeyError, ValueError, TypeError):  # TypeError: non-str or naive timestamp
                fresh = False
            if fresh:
                tools[name] = hit.get("tools", [])
                continue
        try:
            tools[name] = fetch_tools(cfg, timeout=timeout, root=config.root)
            cache[name] = {"key": key, "fetched_at": now.isoformat(), "tools": tools[name]}
            changed = True
        except Exception as exc:  # report, don't crash collection
            errors[name] = f"{type(exc).__name__}: {exc}"
    if changed:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"version": 1, "servers": cache}, indent=2), encoding="utf-8")
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return build_plan(tools, config, errors)
