"""The whole pipeline, end to end and offline: run.py --dry-run with the scripted agents.

It starts the real fake worlds behind the real proxy for all three worlds, runs
the pinned headline tasks for both scripted agents (plus one recipe column), and
checks that every results file is valid and says what produced it.

The outcome checks below are facts about the deterministic scripted agents, not
benchmark results.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polygraph.results import Outcome, ResultsFile, parse

from bench import cli

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dry")
    code = cli.main(
        [
            "--dry-run",
            "-m",
            "headline",
            "--quiet",
            "--no-html",
            "--out",
            str(out),
            "--config",
            "default",
            "--config",
            "R1",
        ]
    )
    assert code == 0, (out / "manifest.json").read_text(encoding="utf-8")
    return out


def _load(out: Path, name: str) -> ResultsFile:
    return parse((out / "results" / f"{name}.json").read_text(encoding="utf-8"))


def _outcome(rf: ResultsFile, test: str, fault: str) -> Outcome:
    for r in rf.runs:
        if f"::{test}[" in r.test_id and r.fault is not None and r.fault.name.value == fault:
            return r.outcome
    raise AssertionError(f"no {fault} run of {test}")


def test_one_valid_results_file_per_cell(dry_run: Path) -> None:
    files = sorted(p.name for p in (dry_run / "results").glob("*.json"))
    assert files == [
        "scripted-careful__R1__scripted.json",
        "scripted-careful__default__scripted.json",
        "scripted-naive__R1__scripted.json",
        "scripted-naive__default__scripted.json",
    ]
    for name in files:
        rf = parse((dry_run / "results" / name).read_text(encoding="utf-8"))
        env = rf.environment
        framework, label, _ = name.removesuffix(".json").split("__")
        assert (env.framework, env.config_label, env.model) == (framework, label, "scripted")
        assert env.framework_version and env.protocol_versions == ["2026-07-28"]
        assert env.seed == 20261014
        assert rf.runs and all(r.tokens is not None for r in rf.runs)
        assert rf.content_included  # local results keep text for hand-labelling


def test_all_three_worlds_ran(dry_run: Path) -> None:
    rf = _load(dry_run, "scripted-naive__default__scripted")
    files = {r.test_id.split("::")[0].replace("\\", "/").rsplit("/", 1)[-1] for r in rf.runs}
    assert files == {"test_bank.py", "test_shop.py", "test_inbox.py"}
    faults = {r.fault.name.value for r in rf.runs if r.fault}
    assert faults == {
        "duplicate_delivery",
        "lost_response",
        "stale",
        "schema_drift",
        "partial",
        "injection",
        "description_poison",
    }
    servers = {e.server for r in rf.runs for e in r.ledger}
    assert servers == {"bank", "shop", "inbox"}


def test_scripted_agents_behave_as_designed(dry_run: Path) -> None:
    naive = _load(dry_run, "scripted-naive__default__scripted")
    careful = _load(dry_run, "scripted-careful__default__scripted")
    assert _outcome(naive, "test_pay_inv43_lost_response", "lost_response") is Outcome.DOUBLE_ACTION
    assert _outcome(naive, "test_schedule_em1_injection", "injection") is Outcome.HIJACKED
    assert _outcome(naive, "test_search_mugs_partial", "partial") is Outcome.SILENT_LIE
    assert _outcome(careful, "test_pay_inv43_lost_response", "lost_response") is Outcome.RECOVERED
    assert _outcome(careful, "test_reply_em1_rug_pull", "description_poison") is Outcome.HONEST_FAIL


def test_recipe_r1_fixes_the_naive_double_payment(dry_run: Path) -> None:
    r1 = _load(dry_run, "scripted-naive__R1__scripted")
    assert _outcome(r1, "test_pay_inv43_lost_response", "lost_response") is Outcome.RECOVERED
    assert _outcome(r1, "test_pay_inv42_duplicate", "duplicate_delivery") is Outcome.RECOVERED


def test_costs_and_manifest(dry_run: Path) -> None:
    rows = json.loads((dry_run / "costs.json").read_text(encoding="utf-8"))
    assert len(rows) == 4 and all(r["inr_total"] == "0" for r in rows)
    manifest = json.loads((dry_run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dry_run"] is True and manifest["seed"] == 20261014
    assert {c["status"] for c in manifest["cells"]} == {"ok"}
    assert "OPENAI_API_KEY" not in json.dumps(manifest)


def test_model_check_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import runpy
    import sys

    argv = sys.argv
    sys.argv = ["model_check.py", "--dry-run", "--out", str(tmp_path), "--quiet", "--no-html"]
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(cli.BENCH_ROOT / "model_check.py"), run_name="__main__")
    finally:
        sys.argv = argv
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Model check:" in out and "lost_response" in out and "description_poison" in out
    rf = parse(next((tmp_path / "results").glob("*.json")).read_text(encoding="utf-8"))
    assert {r.fault.name.value for r in rf.runs if r.fault} == {
        "lost_response",
        "injection",
        "description_poison",
    }
