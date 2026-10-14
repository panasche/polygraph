"""The task catalog: size, worlds, headline-fault coverage, and invented data only."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from polygraph.testing.agents import parse_task

from bench import BENCH_ROOT
from bench.tasks import HEADLINE, MODEL_CHECK_FAULTS, TASKS

TASK_DIR = BENCH_ROOT / "tasks"
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I)


def _decorated_ids() -> dict[str, str]:
    """task id -> test function name, read from the @task("...") decorators."""
    found: dict[str, str] = {}
    for path in sorted(TASK_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and getattr(dec.func, "id", None) == "task"
                        and dec.args
                        and isinstance(dec.args[0], ast.Constant)
                    ):
                        found[str(dec.args[0].value)] = f"{path.name}::{node.name}"
    return found


def test_between_15_and_20_tasks_across_all_three_worlds() -> None:
    assert 15 <= len(TASKS) <= 20
    worlds = {t.world for t in TASKS.values()}
    assert worlds == {"bank", "shop", "inbox"}
    for w in worlds:
        assert sum(t.world == w for t in TASKS.values()) >= 5


def test_every_task_has_exactly_one_test_and_back() -> None:
    decorated = _decorated_ids()
    assert set(decorated) == set(TASKS)
    for task_id, where in decorated.items():
        world = TASKS[task_id].world
        assert where.startswith(f"test_{world}.py::"), (task_id, where)


def test_headline_faults_are_all_pinned() -> None:
    pinned = {
        (p.fault, p.tool, dict(p.params).get("variant"), dict(p.params).get("mode"))
        for p in HEADLINE.values()
    }
    assert ("lost_response", "pay_invoice", None, None) in pinned
    assert ("duplicate_delivery", "pay_invoice", None, None) in pinned
    assert ("partial", "search_products", None, None) in pinned
    assert ("stale", "track_shipment", "conflict", None) in pinned
    assert ("schema_drift", "order_status", None, None) in pinned
    assert ("injection", "read_email", None, None) in pinned  # the injection lands in an email body
    assert ("description_poison", "send_email", None, "rug_pull") in pinned
    assert set(HEADLINE) <= set(TASKS)
    assert {p.fault for p in HEADLINE.values() if p.model_check} == set(MODEL_CHECK_FAULTS)


def test_plain_stale_is_also_planned() -> None:
    import tomllib

    cfg = tomllib.loads((BENCH_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    faults = cfg["tool"]["polygraph"].get("faults", {})
    assert (
        "stale" not in faults
    )  # the planner's stale stays plain; only the pinned task is "conflict"
    assert faults["description_poison"]["mode"] == "rug_pull"


def test_scripted_phrases_are_understood_by_the_reference_agents() -> None:
    for t in TASKS.values():
        parse_task(t.scripted)  # raises TaskNotUnderstood otherwise


def test_only_invented_example_test_addresses() -> None:
    texts = [t.instruction for t in TASKS.values()] + [t.scripted for t in TASKS.values()]
    texts += [p.read_text(encoding="utf-8") for p in TASK_DIR.glob("*.py")]
    for text in texts:
        for address in EMAIL.findall(text):
            assert address.lower().endswith("@example.test"), address


def test_no_secrets_in_the_benchmark_folder() -> None:
    pattern = re.compile(
        r"sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9-]{20,}|pgp_[A-Za-z0-9_-]{16,}|eyJ[\w-]{20,}\."
    )
    for path in BENCH_ROOT.rglob("*"):
        if any(
            part in {".venv", ".venvs", "out", "__pycache__", ".polygraph"} for part in path.parts
        ):
            continue
        if path.is_file() and path.suffix in {
            ".py",
            ".toml",
            ".md",
            ".json",
            ".yml",
            ".csv",
            ".txt",
        }:
            assert not pattern.search(path.read_text(encoding="utf-8", errors="ignore")), path


def test_task_dir_has_no_init() -> None:
    assert not (Path(TASK_DIR) / "__init__.py").exists()  # rootdir-relative conftest, no package
