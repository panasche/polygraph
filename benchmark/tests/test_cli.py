"""run.py / bench.cli: argument checks, the per-cell config file, results validation."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from factories import results, run, write

from bench import cli, configs, models, settings


def test_config_labels() -> None:
    assert configs.resolve("default").is_default
    assert configs.resolve("best-practice").best_practice
    assert configs.resolve("r2").label == "R2"
    assert configs.resolve("R7+R1").recipes == ("R1", "R7")
    assert configs.resolve("all-recipes").recipes == tuple(f"R{i}" for i in range(1, 8))
    for bad in ["R8", "fast", "R1+", "+"]:
        with pytest.raises(ValueError):
            configs.resolve(bad)


def test_model_specs() -> None:
    m = models.parse("openai:some-model")
    assert (m.provider, m.name, m.key_env, str(m)) == (
        "openai",
        "some-model",
        "OPENAI_API_KEY",
        "openai:some-model",
    )
    assert m.slug == "openai_some-model"
    assert models.parse("scripted").is_scripted
    assert models.missing_key(m, {}) == "OPENAI_API_KEY"
    assert models.missing_key(m, {"OPENAI_API_KEY": "x"}) is None
    for bad in ["", "gpt", "bogus:x", "openai:"]:
        with pytest.raises(ValueError):
            models.parse(bad)


def test_settings_from_env() -> None:
    with pytest.raises(RuntimeError, match="POLYGRAPH_BENCH_FRAMEWORK"):
        settings.current({})
    s = settings.current(
        {
            "POLYGRAPH_BENCH_FRAMEWORK": "langgraph",
            "POLYGRAPH_BENCH_MODEL": "openai:m",
            "POLYGRAPH_BENCH_CONFIG": "R2",
        }
    )
    assert (s.framework, s.config_label, str(s.model)) == ("langgraph", "R2", "openai:m")
    env: dict[str, str] = {"POLYGRAPH_MODEL": "kept"}
    settings.export_labels(s, "1.2.3", env)
    assert env == {
        "POLYGRAPH_FRAMEWORK": "langgraph",
        "POLYGRAPH_FRAMEWORK_VERSION": "1.2.3",
        "POLYGRAPH_MODEL": "kept",
        "POLYGRAPH_CONFIG_LABEL": "R2",
    }


def test_cell_config_round_trips_through_toml() -> None:
    base = cli.base_config()
    table = cli.cell_config(
        base,
        framework="scripted-naive",
        config_label="R1",
        model="scripted",
        seed=7,
        judge="rules",
        dry_run=True,
    )
    text = cli.dump_polygraph_toml(table)
    back = tomllib.loads(text)["tool"]["polygraph"]
    assert back["seed"] == 7 and back["judge"] == "rules" and back["config_label"] == "R1"
    assert back["timeout_s"] == 1 and back["faults"]["rate_limit"]["retry_after_s"] == 0.2
    assert back["faults"]["description_poison"]["mode"] == "rug_pull"
    assert set(back["servers"]) == {"bank", "shop", "inbox"}
    assert back["servers"]["bank"]["command"][-1] == "polygraph.worlds.bank"
    llm = cli.cell_config(
        base,
        framework="langgraph",
        config_label="default",
        model="openai:m",
        seed=7,
        judge="llm",
        dry_run=False,
    )
    assert llm["judge"] == "judge.llm_judge:claimed_success"
    assert llm["timeout_s"] == base["timeout_s"]  # real runs keep the configured timings


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--framework", "langgraph"], "no model"),
        (["--model", "openai:m"], "OPENAI_API_KEY"),
        (["--dry-run", "--framework", "langgraph"], "scripted agents only"),
        (["--dry-run", "--judge", "llm"], "offline"),
        (["--dry-run", "--config", "R9"], "unknown config label"),
        (["--dry-run", "--runs", "0"], "at least 1"),
        (["--dry-run", "--python-for", "nope"], "FRAMEWORK=PYTHON"),
        (["--model", "scripted"], "for --dry-run"),
    ],
)
def test_argument_errors(argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv) == 2
    assert message in capsys.readouterr().err


def test_llm_judge_needs_its_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("POLYGRAPH_BENCH_JUDGE_MODEL", "anthropic:j")
    assert cli.main(["--model", "openai:m", "--judge", "llm", "--framework", "langgraph"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_find_python_prefers_override_then_venv() -> None:
    assert cli.find_python("langgraph", {"langgraph": "/x/python"}) == "/x/python"
    import sys

    assert cli.find_python("scripted-naive", {}) == sys.executable


def _cell(tmp_path: Path, **kw: str) -> cli.Cell:
    return cli.Cell(
        framework=kw.get("framework", "langgraph"),
        config_label="default",
        model="openai:fake-model",
        python="python",
        dir=tmp_path,
        results=tmp_path / "r.json",
    )


def test_validate_results(tmp_path: Path) -> None:
    cell = _cell(tmp_path)
    assert cli.validate_results(cell) == "no results file was written"
    write(cell.results, results([run("run_ffffffff1")]))
    assert cli.validate_results(cell) is None
    write(cell.results, results([]))
    assert cli.validate_results(cell) == "results file has no runs"
    write(cell.results, results([run("run_ffffffff2")], framework="pydantic-ai"))
    assert "do not match" in (cli.validate_results(cell) or "")
    cell.results.write_text("{}", encoding="utf-8")
    assert "does not validate" in (cli.validate_results(cell) or "")
