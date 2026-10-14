"""Auto-planning: label priority, name guesses, the §5.3 fault table, ordering, coverage, discovery."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from helpers import http_world, world_tools

import polygraph.planner as planner
from polygraph.config import HintOverride, PolygraphConfig, ServerConfig
from polygraph.planner import (
    CACHE_FILE,
    CACHE_MAX_AGE,
    CHANGING_PREFIXES,
    DESTRUCTIVE_PREFIXES,
    FAMILY_RISK,
    READ_PREFIXES,
    SPEC_DEFAULTS,
    UNAVAILABLE,
    Plan,
    applicable_faults,
    build_plan,
    description_hash,
    discover,
    fetch_tools,
    guess_from_name,
    resolve_hints,
    server_claims,
)
from polygraph.results import (
    FAULT_FAMILY,
    FaultName,
    FaultSpec,
    HintValue,
    LabelSource,
    Outcome,
    Priority,
    Run,
    ToolHints,
)

HINTS = ("read_only", "destructive", "idempotent", "open_world")
ANN = {
    "read_only": "readOnlyHint",
    "destructive": "destructiveHint",
    "idempotent": "idempotentHint",
    "open_world": "openWorldHint",
}
F = FaultName
TOP, NORMAL = Priority.TOP, Priority.NORMAL


def tool(name: str, **ann: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"name": name, "description": f"{name} does a thing"}
    if ann:
        d["annotations"] = {ANN[k]: v for k, v in ann.items()}
    return d


def hints(read_only: bool, destructive: bool, idempotent: bool, open_world: bool) -> ToolHints:
    vals = dict(
        read_only=read_only, destructive=destructive, idempotent=idempotent, open_world=open_world
    )
    return ToolHints(**{k: HintValue(value=v, source=LabelSource.SERVER) for k, v in vals.items()})


def sources(h: ToolHints) -> dict[str, tuple[bool, LabelSource]]:
    return {k: (getattr(h, k).value, getattr(h, k).source) for k in HINTS}


# --------------------------------------------------------------------------- #
# resolve_hints: override > server > guessed > default
# --------------------------------------------------------------------------- #


def test_all_four_from_override():
    cfg = PolygraphConfig(
        overrides={
            "do_thing": HintOverride(
                read_only=True, destructive=False, idempotent=True, open_world=False
            )
        }
    )
    t = tool("do_thing", read_only=False, destructive=True, idempotent=False, open_world=True)
    h = resolve_hints("srv", t, cfg)
    assert sources(h) == {
        "read_only": (True, LabelSource.OVERRIDE),
        "destructive": (False, LabelSource.OVERRIDE),
        "idempotent": (True, LabelSource.OVERRIDE),
        "open_world": (False, LabelSource.OVERRIDE),
    }


def test_server_tool_override_key():
    cfg = PolygraphConfig(
        overrides={
            "do_thing": HintOverride(read_only=False),
            "srv/do_thing": HintOverride(read_only=True),
        }
    )
    assert resolve_hints("srv", tool("do_thing"), cfg).read_only.value is True
    assert resolve_hints("other", tool("do_thing"), cfg).read_only.value is False


def test_all_four_from_server():
    t = tool("do_thing", read_only=True, destructive=False, idempotent=True, open_world=False)
    assert sources(resolve_hints("srv", t)) == {
        "read_only": (True, LabelSource.SERVER),
        "destructive": (False, LabelSource.SERVER),
        "idempotent": (True, LabelSource.SERVER),
        "open_world": (False, LabelSource.SERVER),
    }


def test_server_beats_guess():
    # name says read-only, the server says it changes things
    t = tool("get_and_bump", read_only=False)
    h = resolve_hints("srv", t)
    assert (h.read_only.value, h.read_only.source) == (False, LabelSource.SERVER)
    assert h.destructive.source is LabelSource.GUESSED


def test_partial_override_falls_through_to_server_and_guess():
    cfg = PolygraphConfig(overrides={"get_invoice": HintOverride(idempotent=False)})
    t = tool("get_invoice", read_only=True)
    assert sources(resolve_hints("bank", t, cfg)) == {
        "read_only": (True, LabelSource.SERVER),
        "destructive": (False, LabelSource.GUESSED),
        "idempotent": (False, LabelSource.OVERRIDE),
        "open_world": (True, LabelSource.DEFAULT),
    }


def test_guessed_and_default_sources():
    h = resolve_hints("srv", tool("delete_account"))
    assert sources(h) == {
        "read_only": (False, LabelSource.GUESSED),
        "destructive": (True, LabelSource.GUESSED),
        "idempotent": (False, LabelSource.DEFAULT),
        "open_world": (True, LabelSource.DEFAULT),
    }


def test_no_guess_uses_spec_defaults():
    h = resolve_hints("srv", tool("frobnicate"))
    assert sources(h) == {k: (SPEC_DEFAULTS[k], LabelSource.DEFAULT) for k in HINTS}
    assert SPEC_DEFAULTS == {
        "read_only": False,
        "destructive": True,
        "idempotent": False,
        "open_world": True,
    }


@pytest.mark.parametrize("bad", ["yes", 1, None, "true"])
def test_non_bool_annotation_is_ignored(bad):
    t = {"name": "frobnicate", "annotations": {"readOnlyHint": bad}}
    h = resolve_hints("srv", t)
    assert h.read_only.source is LabelSource.DEFAULT and h.read_only.value is False


@pytest.mark.parametrize("ann", [None, "readOnly", ["readOnlyHint"], 3])
def test_non_dict_annotations_are_ignored(ann):
    h = resolve_hints("srv", {"name": "frobnicate", "annotations": ann})
    assert all(getattr(h, k).source is LabelSource.DEFAULT for k in HINTS)


def test_missing_name_is_default():
    h = resolve_hints("srv", {})
    assert all(getattr(h, k).source is LabelSource.DEFAULT for k in HINTS)


# --------------------------------------------------------------------------- #
# name guessing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prefix", READ_PREFIXES)
def test_read_prefixes(prefix):
    assert guess_from_name(f"{prefix}thing") == {
        "read_only": True,
        "destructive": False,
        "idempotent": True,
    }
    assert guess_from_name(f"{prefix.upper()}THING")["read_only"] is True


@pytest.mark.parametrize("prefix", CHANGING_PREFIXES)
def test_changing_prefixes(prefix):
    g = guess_from_name(f"{prefix}thing")
    assert g["read_only"] is False
    assert "idempotent" not in g and "open_world" not in g
    if prefix in DESTRUCTIVE_PREFIXES:
        assert g["destructive"] is True
    else:
        assert "destructive" not in g


@pytest.mark.parametrize("prefix", DESTRUCTIVE_PREFIXES)
def test_destructive_prefixes(prefix):
    assert guess_from_name(f"{prefix}thing") == {"read_only": False, "destructive": True}


def test_research_doc_prefixes_are_present():
    for p in ("pay_", "send_", "create_", "update_", "delete_"):
        assert p in CHANGING_PREFIXES
    for p in ("get_", "list_", "search_", "read_"):
        assert p in READ_PREFIXES


@pytest.mark.parametrize("name", ["frobnicate", "refund", "getter", "pay", "", "order_status"])
def test_no_guess(name):
    assert guess_from_name(name) == {}


# --------------------------------------------------------------------------- #
# the §5.3 table
# --------------------------------------------------------------------------- #

UNAV = {f: NORMAL for f in UNAVAILABLE}


def test_unavailable_family_is_the_five():
    assert set(UNAVAILABLE) == {F.TIMEOUT, F.SLOW, F.ERROR, F.RATE_LIMIT, F.AUTH_EXPIRED}
    assert all(FAULT_FAMILY[f].value == "unavailable" for f in UNAVAILABLE)


def test_read_only_closed_world():
    got = dict(applicable_faults(hints(True, False, True, False)))
    assert got == {
        **UNAV,
        F.PARTIAL: NORMAL,
        F.MALFORMED: NORMAL,
        F.SCHEMA_DRIFT: NORMAL,
        F.STALE: NORMAL,
        F.INJECTION: NORMAL,
        F.DESCRIPTION_POISON: NORMAL,
    }


def test_read_only_open_world_makes_injection_top():
    got = dict(applicable_faults(hints(True, False, True, True)))
    assert got[F.INJECTION] is TOP
    assert set(got) == set(dict(applicable_faults(hints(True, False, True, False))))


CHANGING_CLOSED = {
    **UNAV,
    F.MALFORMED: NORMAL,
    F.LOST_RESPONSE: TOP,
    F.DUPLICATE_DELIVERY: TOP,
    F.DESCRIPTION_POISON: NORMAL,
}


@pytest.mark.parametrize("destructive", [True, False])
def test_changing_not_idempotent(destructive):
    assert dict(applicable_faults(hints(False, destructive, False, False))) == CHANGING_CLOSED


def test_changing_says_idempotent_is_the_same():
    assert dict(applicable_faults(hints(False, False, True, False))) == CHANGING_CLOSED


@pytest.mark.parametrize("idempotent", [True, False])
def test_changing_open_world_adds_top_injection(idempotent):
    got = dict(applicable_faults(hints(False, True, idempotent, True)))
    assert got == {**CHANGING_CLOSED, F.INJECTION: TOP}


@pytest.mark.parametrize("ro", [True, False])
@pytest.mark.parametrize("ow", [True, False])
def test_every_tool_gets_description_poison_and_order(ro, ow):
    got = applicable_faults(hints(ro, True, False, ow))
    assert (F.DESCRIPTION_POISON, NORMAL) in got
    order = list(FaultName)
    assert [f for f, _ in got] == sorted((f for f, _ in got), key=order.index)
    assert len({f for f, _ in got}) == len(got)


def test_read_only_never_gets_one_time_faults():
    got = dict(applicable_faults(hints(True, True, False, True)))
    assert F.LOST_RESPONSE not in got and F.DUPLICATE_DELIVERY not in got


# --------------------------------------------------------------------------- #
# server_claims
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ann", "claims"),
    [
        ({"idempotentHint": True}, {"idempotent"}),
        ({"idempotentHint": True, "readOnlyHint": False}, {"idempotent"}),
        ({"idempotentHint": True, "readOnlyHint": True}, set()),
        ({"idempotentHint": False}, set()),
        ({"idempotentHint": "true"}, set()),
        ({}, set()),
        (None, set()),
        ("idempotentHint", set()),
    ],
)
def test_server_claims(ann, claims):
    t: dict[str, Any] = {"name": "x"}
    if ann is not None:
        t["annotations"] = ann
    assert server_claims(t) == claims


def test_override_does_not_create_a_claim():
    cfg = PolygraphConfig(overrides={"x": HintOverride(idempotent=True)})
    plan = build_plan({"s": [tool("x")]}, cfg)
    assert plan.tools[0].claims == set()
    assert plan.tools[0].info.hints.idempotent.value is True


# --------------------------------------------------------------------------- #
# Plan: ordering, find, matrix, coverage, build_plan
# --------------------------------------------------------------------------- #


def mixed_plan() -> Plan:
    return build_plan(
        {
            "alpha": [
                tool("get_record", read_only=True, open_world=False),
                tool("pay_bill", open_world=False),
                tool("archive_bill", read_only=False, destructive=False, open_world=False),
                tool(
                    "refund_bill",
                    read_only=False,
                    destructive=False,
                    idempotent=True,
                    open_world=False,
                ),
            ],
            "beta": [tool("pay_bill", open_world=False), tool("search_web")],
        }
    )


def test_pairs_riskiest_first():
    pairs = mixed_plan().pairs()
    ranks = [(0 if p is TOP else 1, FAMILY_RISK[FAULT_FAMILY[f]]) for _t, f, p in pairs]
    assert ranks == sorted(ranks)
    # top priority first, divergent before hostile
    first_normal = next(i for i, (_t, _f, p) in enumerate(pairs) if p is NORMAL)
    assert all(p is TOP for _t, _f, p in pairs[:first_normal])
    fams = [FAULT_FAMILY[f].value for _t, f, p in pairs]
    assert fams[0] == "divergent"
    assert "hostile" in fams[:first_normal]
    # then hostile, corrupt, unavailable among the normal ones
    normal_fams = list(dict.fromkeys(fams[first_normal:]))
    assert normal_fams == ["hostile", "corrupt", "unavailable"]


def test_pairs_tie_break_on_tool_kind():
    pairs = mixed_plan().pairs()
    timeouts = [t.key for t, f, _p in pairs if f is F.TIMEOUT]
    assert timeouts == [
        # changing, destructive, not idempotent (by server then name)
        "alpha/pay_bill",
        "beta/pay_bill",
        # changing, not destructive, not idempotent
        "alpha/archive_bill",
        # changing, not destructive, idempotent
        "alpha/refund_bill",
        # read-only: guessed destructive=False, idempotent=True
        "alpha/get_record",
        "beta/search_web",
    ]


def test_pairs_same_tool_follow_fault_order():
    plan = build_plan({"s": [tool("pay_bill", open_world=False)]})
    tops = [f for _t, f, p in plan.pairs() if p is TOP]
    assert tops == [F.LOST_RESPONSE, F.DUPLICATE_DELIVERY]


def test_find_and_tool_infos():
    plan = mixed_plan()
    assert plan.find("pay_bill").server == "alpha"
    assert plan.find("pay_bill", "beta").key == "beta/pay_bill"
    assert plan.find("pay_bill", "gamma") is None
    assert plan.find("nope") is None
    assert [i.key for i in plan.tool_infos] == [t.key for t in plan.tools]
    assert plan.tools[0].name == "get_record"


def test_matrix_unfiltered_matches_pairs():
    plan = mixed_plan()
    m = plan.matrix()
    assert [(s.server, s.tool, s.name) for s in m] == [
        (t.server, t.name, f) for t, f, _p in plan.pairs()
    ]
    assert all(isinstance(s, FaultSpec) and s.params == {} and s.on_call == 1 for s in m)


def test_matrix_filters():
    plan = mixed_plan()
    assert {s.server for s in plan.matrix(servers=["beta"])} == {"beta"}
    only = plan.matrix(faults=["lost_response", "injection"])
    assert {s.name for s in only} == {F.LOST_RESPONSE, F.INJECTION}
    by_name = plan.matrix(tools=["pay_bill"])
    assert {(s.server, s.tool) for s in by_name} == {("alpha", "pay_bill"), ("beta", "pay_bill")}
    by_key = plan.matrix(tools=["beta/pay_bill"])
    assert {(s.server, s.tool) for s in by_key} == {("beta", "pay_bill")}
    combo = plan.matrix(servers=["alpha"], faults=["timeout"], tools=["alpha/get_record"])
    assert [(s.tool, s.name) for s in combo] == [("get_record", F.TIMEOUT)]
    assert plan.matrix(servers=[]) == []


def test_matrix_params_are_copied_per_fault():
    plan = mixed_plan()
    params = {"rate_limit": {"retry_after_s": 10}}
    m = plan.matrix(faults=["rate_limit", "timeout"], params=params)
    rl = [s for s in m if s.name is F.RATE_LIMIT]
    assert rl and all(s.params == {"retry_after_s": 10} for s in rl)
    assert all(s.params == {} for s in m if s.name is F.TIMEOUT)
    rl[0].params["retry_after_s"] = 99
    assert params["rate_limit"]["retry_after_s"] == 10
    assert rl[1].params["retry_after_s"] == 10


def make_run(i: int, fault: FaultSpec | None) -> Run:
    return Run(
        id=f"run-{i:06d}",
        test_id="tests/test_x.py::test_pay",
        fault=fault,
        seed=1,
        repeat=i,
        started_at=datetime(2026, 10, 13, tzinfo=UTC),
        duration_ms=1.0,
        outcome=Outcome.RECOVERED,
    )


def test_coverage_counts_runs():
    plan = build_plan({"bank": [tool("pay_bill", open_world=False)]})
    lr = FaultSpec(name=F.LOST_RESPONSE, server="bank", tool="pay_bill")
    dd = FaultSpec(name=F.DUPLICATE_DELIVERY, server="bank", tool="pay_bill")
    other = FaultSpec(name=F.TIMEOUT, server="shop", tool="pay_bill")
    runs = [
        make_run(0, lr),
        make_run(1, lr),
        make_run(2, dd),
        make_run(3, None),
        make_run(4, other),
    ]
    cov = plan.coverage(runs, runs_required=2)
    assert cov.applicable == len(plan.pairs()) == 9
    by = {(p.server, p.tool, p.fault): p for p in cov.pairs}
    assert by[("bank", "pay_bill", F.LOST_RESPONSE)].runs_done == 2
    assert by[("bank", "pay_bill", F.LOST_RESPONSE)].covered
    assert by[("bank", "pay_bill", F.DUPLICATE_DELIVERY)].runs_done == 1
    assert not by[("bank", "pay_bill", F.DUPLICATE_DELIVERY)].covered
    assert by[("bank", "pay_bill", F.TIMEOUT)].runs_done == 0
    assert cov.covered == 1
    assert all(p.runs_required == 2 for p in cov.pairs)
    assert [p.fault for p in cov.pairs][:2] == [F.LOST_RESPONSE, F.DUPLICATE_DELIVERY]
    assert cov.pairs[0].priority is TOP
    assert plan.coverage(runs, runs_required=1).covered == 2


def test_coverage_empty():
    cov = Plan().coverage([], runs_required=3)
    assert (cov.applicable, cov.covered, cov.pairs, cov.ratio) == (0, 0, [], None)


def test_build_plan_skips_junk_and_keeps_errors():
    tools = [
        "not a dict",
        {"description": "no name"},
        {"name": 42},
        None,
        {"name": "get_x", "description": "Look up x.", "outputSchema": {"type": "object"}},
        {"name": "pay_x", "outputSchema": "nope"},
    ]
    plan = build_plan({"s": tools}, errors={"down": "OSError: boom"})  # type: ignore[dict-item]
    assert [t.name for t in plan.tools] == ["get_x", "pay_x"]
    assert plan.errors == {"down": "OSError: boom"}
    get_x, pay_x = plan.tools
    assert get_x.info.has_output_schema is True and pay_x.info.has_output_schema is False
    assert get_x.info.description_sha256 == description_hash(tools[4])
    assert pay_x.info.description_sha256 == description_hash({})
    assert get_x.definition == tools[4] and get_x.definition is not tools[4]
    assert get_x.key == "s/get_x"


def test_description_hash():
    import hashlib

    assert description_hash({"description": "abc"}) == hashlib.sha256(b"abc").hexdigest()
    assert description_hash({"description": None}) == hashlib.sha256(b"").hexdigest()


# --------------------------------------------------------------------------- #
# the three fake worlds
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def world_defs(tmp_path_factory) -> dict[str, list[dict[str, Any]]]:
    tmp = tmp_path_factory.mktemp("worlds")
    return {w: world_tools(w, tmp) for w in ("bank", "shop", "inbox")}


def src(plan: Plan, key: str, hint: str) -> tuple[bool, LabelSource]:
    server, name = key.split("/")
    t = plan.find(name, server)
    assert t is not None, key
    h = getattr(t.info.hints, hint)
    return h.value, h.source


S, G, D, OV = LabelSource.SERVER, LabelSource.GUESSED, LabelSource.DEFAULT, LabelSource.OVERRIDE


def test_world_labels(world_defs):
    plan = build_plan(world_defs)
    assert src(plan, "bank/get_invoice", "read_only") == (True, S)
    assert src(plan, "bank/get_invoice", "destructive") == (False, G)
    assert src(plan, "bank/get_invoice", "open_world") == (False, S)
    assert src(plan, "bank/pay_invoice", "read_only") == (False, G)
    assert src(plan, "bank/pay_invoice", "destructive") == (True, D)
    assert src(plan, "bank/pay_invoice", "idempotent") == (False, D)
    assert src(plan, "bank/pay_invoice", "open_world") == (True, D)
    assert src(plan, "bank/refund", "idempotent") == (True, S)
    assert src(plan, "bank/refund", "read_only") == (False, S)
    assert src(plan, "inbox/send_email", "read_only") == (False, G)
    assert src(plan, "inbox/read_email", "read_only") == (True, S)
    assert src(plan, "shop/search_products", "read_only") == (True, G)
    assert src(plan, "shop/search_products", "idempotent") == (True, G)
    assert src(plan, "shop/place_order", "read_only") == (False, S)


def test_world_claims(world_defs):
    plan = build_plan(world_defs)
    claims = {t.key: t.claims for t in plan.tools}
    assert claims["bank/refund"] == {"idempotent"}
    assert all(c == set() for k, c in claims.items() if k != "bank/refund")


def test_world_riskiest_pair_is_pay_invoice_lost_response(world_defs):
    plan = build_plan({"bank": world_defs["bank"]})
    t, f, p = plan.pairs()[0]
    assert (t.key, f, p) == ("bank/pay_invoice", F.LOST_RESPONSE, TOP)


def test_world_fault_sets(world_defs):
    plan = build_plan(world_defs)
    faults = {t.key: dict(t.faults) for t in plan.tools}
    assert faults["bank/get_invoice"][F.INJECTION] is NORMAL  # closed world read-only
    assert faults["inbox/read_email"][F.INJECTION] is TOP  # open world read-only
    assert faults["bank/pay_invoice"][F.INJECTION] is TOP  # default open world
    assert F.INJECTION not in faults["bank/refund"]
    assert faults["bank/refund"][F.DUPLICATE_DELIVERY] is TOP


def test_world_override_wins(world_defs):
    cfg = PolygraphConfig(
        overrides={
            "refund": HintOverride(idempotent=False),
            "bank/pay_invoice": HintOverride(read_only=True, open_world=False),
        }
    )
    plan = build_plan(world_defs, cfg)
    assert src(plan, "bank/refund", "idempotent") == (False, OV)
    assert src(plan, "bank/pay_invoice", "read_only") == (True, OV)
    assert src(plan, "bank/pay_invoice", "open_world") == (False, OV)
    # the server's claim is still checked even when the override disagrees
    assert plan.find("refund", "bank").claims == {"idempotent"}
    assert F.LOST_RESPONSE not in dict(plan.find("pay_invoice", "bank").faults)


# --------------------------------------------------------------------------- #
# discover() and the tools cache
# --------------------------------------------------------------------------- #

BANK_CMD = [sys.executable, "-m", "polygraph.worlds.bank"]
FAKE_TOOLS = [{"name": "get_thing"}, {"name": "pay_thing"}]


def bank_config(tmp_path, **server: Any) -> PolygraphConfig:
    return PolygraphConfig(
        root=tmp_path, servers={"bank": ServerConfig(command=BANK_CMD, **server)}
    )


def boom(*a: Any, **kw: Any):
    raise AssertionError("fetch_tools must not be called")


class Counter:
    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[ServerConfig] = []
        self.tools = tools if tools is not None else FAKE_TOOLS

    def __call__(self, cfg: ServerConfig, *, timeout: float = 30.0, root=None):
        self.calls.append(cfg)
        return [dict(t) for t in self.tools]


def read_cache(tmp_path) -> dict[str, Any]:
    return json.loads((tmp_path / ".polygraph" / CACHE_FILE).read_text(encoding="utf-8"))


@pytest.mark.integration
def test_discover_real_world_then_cache(tmp_path, monkeypatch):
    cfg = bank_config(tmp_path)
    plan = discover(cfg, timeout=20)
    assert plan.errors == {}
    assert {t.name for t in plan.tools} == {"get_invoice", "pay_invoice", "refund"}
    data = read_cache(tmp_path)
    assert data["version"] == 1
    entry = data["servers"]["bank"]
    assert {t["name"] for t in entry["tools"]} == {"get_invoice", "pay_invoice", "refund"}
    assert datetime.fromisoformat(entry["fetched_at"]).tzinfo is not None
    assert not list((tmp_path / ".polygraph").glob("*.tmp"))

    monkeypatch.setattr(planner, "fetch_tools", boom)
    again = discover(cfg)
    assert again.errors == {}
    assert [t.key for t in again.tools] == [t.key for t in plan.tools]
    assert again.find("refund").claims == {"idempotent"}


@pytest.mark.integration
def test_fetch_tools_over_http(tmp_path):
    with http_world("shop", tmp_path / "shop.sqlite") as (url, _db):
        tools = fetch_tools(ServerConfig(url=url), timeout=10)
    assert {"search_products", "place_order"} <= {t["name"] for t in tools}


@pytest.mark.integration
def test_discover_failing_server_lands_in_errors(tmp_path):
    cfg = PolygraphConfig(
        root=tmp_path,
        servers={"dead": ServerConfig(command=[sys.executable, "-c", "import sys; sys.exit(3)"])},
    )
    plan = discover(cfg, timeout=10)
    assert plan.tools == []
    assert set(plan.errors) == {"dead"}
    assert plan.errors["dead"].split(":", 1)[0].isidentifier()
    assert not (tmp_path / ".polygraph" / CACHE_FILE).exists()


def test_discover_error_from_fetch_is_reported(tmp_path, monkeypatch):
    def fail(cfg, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(planner, "fetch_tools", fail)
    plan = discover(bank_config(tmp_path))
    assert plan.errors == {"bank": "ConnectionError: refused"}


def test_discover_changed_config_refetches(tmp_path, monkeypatch):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    discover(bank_config(tmp_path))
    discover(bank_config(tmp_path))
    assert len(fake.calls) == 1
    discover(bank_config(tmp_path, env={"MODE": "other"}))
    assert len(fake.calls) == 2
    assert fake.calls[1].env == {"MODE": "other"}


def test_discover_refresh_refetches(tmp_path, monkeypatch):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    cfg = bank_config(tmp_path)
    discover(cfg)
    discover(cfg, refresh=True)
    assert len(fake.calls) == 2


def test_discover_stale_cache_refetches(tmp_path, monkeypatch):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    cfg = bank_config(tmp_path)
    discover(cfg)
    data = read_cache(tmp_path)
    old = datetime.now(UTC) - CACHE_MAX_AGE - timedelta(minutes=1)
    data["servers"]["bank"]["fetched_at"] = old.isoformat()
    (tmp_path / ".polygraph" / CACHE_FILE).write_text(json.dumps(data), encoding="utf-8")
    discover(cfg)
    assert len(fake.calls) == 2
    fresh = datetime.fromisoformat(read_cache(tmp_path)["servers"]["bank"]["fetched_at"])
    assert datetime.now(UTC) - fresh < timedelta(minutes=1)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda e: e.pop("fetched_at"),
        lambda e: e.update(fetched_at="yesterday"),
        lambda e: e.update(fetched_at="2026-10-13T00:00:00"),  # no timezone
    ],
    ids=["missing", "garbage", "naive"],
)
def test_discover_bad_fetched_at_refetches(tmp_path, monkeypatch, mangle):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    cfg = bank_config(tmp_path)
    discover(cfg)
    data = read_cache(tmp_path)
    mangle(data["servers"]["bank"])
    (tmp_path / ".polygraph" / CACHE_FILE).write_text(json.dumps(data), encoding="utf-8")
    plan = discover(cfg)
    assert plan.errors == {}
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"servers": []}',
        '{"servers": {"bank": "junk"}}',
        '{"servers": {"bank": {"key": null, "fetched_at": 5}}}',
    ],
)
def test_discover_unreadable_cache_is_ignored(tmp_path, monkeypatch, content):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    (tmp_path / ".polygraph").mkdir()
    (tmp_path / ".polygraph" / CACHE_FILE).write_text(content, encoding="utf-8")
    plan = discover(bank_config(tmp_path))
    assert [t.name for t in plan.tools] == ["get_thing", "pay_thing"]
    assert len(fake.calls) == 1
    assert read_cache(tmp_path)["servers"]["bank"]["tools"] == FAKE_TOOLS


def test_discover_servers_filter(tmp_path, monkeypatch):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    cfg = PolygraphConfig(
        root=tmp_path,
        servers={
            "bank": ServerConfig(command=BANK_CMD),
            "shop": ServerConfig(url="http://127.0.0.1:9/mcp"),
        },
    )
    plan = discover(cfg, servers=["shop"])
    assert {t.server for t in plan.tools} == {"shop"}
    assert [c.url for c in fake.calls] == ["http://127.0.0.1:9/mcp"]


def test_discover_custom_cache_dir_and_unwritable(tmp_path, monkeypatch):
    fake = Counter()
    monkeypatch.setattr(planner, "fetch_tools", fake)
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    plan = discover(bank_config(tmp_path), cache_dir=blocker / "cache")
    assert plan.errors == {} and len(plan.tools) == 2  # a cache it cannot write is not fatal
    custom = tmp_path / "custom"
    discover(bank_config(tmp_path), cache_dir=custom)
    assert (custom / CACHE_FILE).exists()
    assert not (tmp_path / ".polygraph").exists()


def test_fetch_tools_stdio_connection(tmp_path, monkeypatch):
    seen: dict[str, Any] = {}

    class FakeClient:
        @classmethod
        def connect(cls, conn, **kw):
            seen.update(conn=conn, kw=kw)
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def list_tools(self, refresh=False):
            seen["refresh"] = refresh
            return list(FAKE_TOOLS)

    import polygraph.testing.client as client_mod

    monkeypatch.setattr(client_mod, "McpClient", FakeClient)
    cfg = ServerConfig(command=["python", "-m", "srv"], env={"A": "1"})
    assert fetch_tools(cfg, timeout=3, root=tmp_path) == FAKE_TOOLS
    conn = seen["conn"]
    assert conn["command"] == [sys.executable, "-m", "srv"]
    assert conn["cwd"] == str(tmp_path)
    assert conn["env"]["A"] == "1" and "POLYGRAPH_RUN_DIR" in conn["env"]
    assert seen["kw"] == {"timeout": 3, "client_name": "polygraph-planner"}
    assert seen["refresh"] is True

    fetch_tools(ServerConfig(command=["node", "x.js"], cwd="elsewhere"))
    assert seen["conn"]["cwd"] == "elsewhere"
    fetch_tools(ServerConfig(command=["node", "x.js"]))
    assert seen["conn"]["cwd"] is None

    fetch_tools(ServerConfig(url="http://127.0.0.1:9/mcp", headers={"X-Test": "1"}))
    assert seen["conn"] == {"url": "http://127.0.0.1:9/mcp", "headers": {"X-Test": "1"}}
