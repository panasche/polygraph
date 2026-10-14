"""``[tool.polygraph]`` loading, validation and helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from polygraph.config import (
    ConfigError,
    HintOverride,
    PolygraphConfig,
    ServerConfig,
    find_pyproject,
    load_config,
    resolve_command,
)

FULL = """
[project]
name = "example"

[tool.polygraph]
runs = 5
seed = 1234
loop_threshold = 3
timeout_s = 30
slow_ms = 8000
judge = "rules"
mask = "module.path:mask"

[tool.polygraph.servers]
bank = { command = ["python", "-m", "polygraph.worlds.bank"] }
shop = { url = "http://localhost:8001/mcp" }

[tool.polygraph.overrides]
refund = { read_only = false, idempotent = false }

[tool.polygraph.faults.rate_limit]
retry_after_s = 10
"""


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_full_example(tmp_path):
    p = write(tmp_path, FULL)
    cfg = load_config(p)
    assert cfg.runs == 5
    assert cfg.seed == 1234
    assert cfg.loop_threshold == 3
    assert cfg.timeout_s == 30.0
    assert cfg.slow_ms == 8000
    assert cfg.judge == "rules"
    assert cfg.mask == "module.path:mask"
    assert cfg.root == tmp_path.resolve()
    bank, shop = cfg.servers["bank"], cfg.servers["shop"]
    assert bank.command == ["python", "-m", "polygraph.worlds.bank"]
    assert bank.transport == "stdio"
    assert shop.url == "http://localhost:8001/mcp"
    assert shop.transport == "http"
    assert cfg.overrides["refund"] == HintOverride(read_only=False, idempotent=False)
    assert cfg.overrides["refund"].destructive is None
    assert cfg.faults == {"rate_limit": {"retry_after_s": 10}}


def test_load_by_start_dir(tmp_path):
    write(tmp_path, FULL)
    assert load_config(start=tmp_path).runs == 5


def test_root_is_not_dumped(tmp_path):
    cfg = load_config(write(tmp_path, FULL))
    assert "root" not in cfg.model_dump()


def test_defaults_when_no_file(tmp_path):
    sub = tmp_path / "nothing-here"
    sub.mkdir()
    cfg = load_config(sub / "pyproject.toml")
    assert cfg.runs == 1
    assert cfg.seed is None
    assert cfg.loop_threshold == 3
    assert cfg.timeout_s == 30.0
    assert cfg.slow_ms == 8000
    assert cfg.judge == "rules"
    assert cfg.mask is None
    assert cfg.servers == {} and cfg.overrides == {} and cfg.faults == {}


def test_defaults_root_when_nothing_found(tmp_path, monkeypatch):
    import polygraph.config as config_mod

    monkeypatch.setattr(config_mod, "find_pyproject", lambda start=None: None)
    cfg = load_config(start=tmp_path)
    assert cfg.root == tmp_path.resolve()
    assert cfg.runs == 1


def test_defaults_when_no_table(tmp_path):
    cfg = load_config(write(tmp_path, '[project]\nname = "x"\n'))
    assert cfg.runs == 1 and cfg.servers == {}
    assert cfg.root == tmp_path.resolve()


def test_find_pyproject_walks_up(tmp_path):
    p = write(tmp_path, FULL)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_pyproject(deep) == p.resolve()
    cfg = load_config(start=deep)
    assert cfg.runs == 5
    assert cfg.root == tmp_path.resolve()


def test_find_pyproject_prefers_nearest(tmp_path):
    write(tmp_path, FULL)
    inner = tmp_path / "inner"
    inner.mkdir()
    nearest = write(inner, "[tool.polygraph]\nruns = 2\n")
    assert find_pyproject(inner) == nearest.resolve()
    assert load_config(start=inner).runs == 2


def test_find_pyproject_ignores_directory_named_pyproject(tmp_path):
    real = write(tmp_path, FULL)
    sub = tmp_path / "sub"
    (sub / "pyproject.toml").mkdir(parents=True)
    assert find_pyproject(sub) == real.resolve()


def test_find_pyproject_none_when_nothing_up_the_tree(tmp_path, monkeypatch):
    import polygraph.config as config_mod

    monkeypatch.setattr(config_mod, "PYPROJECT", "polygraph-test-no-such-file.toml")
    assert find_pyproject(tmp_path) is None
    cfg = load_config(start=tmp_path)
    assert cfg.root == tmp_path.resolve() and cfg.runs == 1


def test_find_pyproject_defaults_to_cwd(tmp_path, monkeypatch):
    p = write(tmp_path, FULL)
    monkeypatch.chdir(tmp_path)
    assert find_pyproject() == p.resolve()


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("[tool.polygraph]\nrunz = 3\n", "runz"),
        ("[tool.polygraph.servers]\nbank = { command = ['x'], colour = 'red' }\n", "colour"),
        ("[tool.polygraph.overrides]\nrefund = { readonly = true }\n", "readonly"),
        (
            "[tool.polygraph.servers]\nbank = { command = ['x'], url = 'http://h/mcp' }\n",
            "exactly one of 'command'",
        ),
        ("[tool.polygraph.servers]\nbank = { env = { A = 'b' } }\n", "exactly one of 'command'"),
        ("[tool.polygraph.servers]\nbank = { command = [] }\n", "'command' must not be empty"),
        ("[tool.polygraph.faults.meltdown]\nx = 1\n", "meltdown"),
        ("[tool.polygraph]\njudge = 'gpt'\n", "judge must be 'rules'"),
        ("[tool.polygraph]\nruns = 0\n", "runs"),
        ("[tool.polygraph]\nloop_threshold = 1\n", "loop_threshold"),
        ("[tool.polygraph]\ntimeout_s = 0\n", "timeout_s"),
    ],
)
def test_invalid_config_raises_config_error(tmp_path, body, needle):
    p = write(tmp_path, body)
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    msg = str(ei.value)
    assert needle in msg
    assert "[tool.polygraph]" in msg
    assert str(p) in msg


def test_unknown_fault_lists_all_bad_names(tmp_path):
    p = write(tmp_path, "[tool.polygraph.faults.zeta]\n[tool.polygraph.faults.alpha]\n")
    with pytest.raises(ConfigError, match="alpha, zeta"):
        load_config(p)


def test_bad_toml_raises_config_error(tmp_path):
    p = write(tmp_path, "[tool.polygraph\nruns = = 3\n")
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert str(p) in str(ei.value)
    assert isinstance(ei.value, ValueError)


def test_server_config_direct():
    with pytest.raises(ValueError):
        ServerConfig()
    assert ServerConfig(url="http://h/mcp").transport == "http"


def test_judge_module_spec_is_accepted():
    assert PolygraphConfig(judge="pkg.mod:fn").judge == "pkg.mod:fn"


def test_override_for_server_tool_beats_tool():
    cfg = PolygraphConfig(
        overrides={
            "refund": HintOverride(idempotent=False),
            "bank/refund": HintOverride(idempotent=True),
        }
    )
    assert cfg.override_for("bank", "refund").idempotent is True
    assert cfg.override_for("shop", "refund").idempotent is False
    assert cfg.override_for("bank", "pay_invoice") is None


def test_override_for_tool_only():
    cfg = PolygraphConfig(overrides={"refund": HintOverride(read_only=True)})
    assert cfg.override_for("anything", "refund").read_only is True


LABEL_ENV = {
    "POLYGRAPH_FRAMEWORK": "framework",
    "POLYGRAPH_FRAMEWORK_VERSION": "framework_version",
    "POLYGRAPH_MODEL": "model",
    "POLYGRAPH_CONFIG_LABEL": "config_label",
}


def test_labels_from_config(monkeypatch):
    for var in LABEL_ENV:
        monkeypatch.delenv(var, raising=False)
    cfg = PolygraphConfig(
        framework="langgraph", framework_version="0.2", model="m-1", config_label="default"
    )
    assert cfg.labels() == {
        "framework": "langgraph",
        "framework_version": "0.2",
        "model": "m-1",
        "config_label": "default",
    }


def test_labels_env_overrides(monkeypatch):
    cfg = PolygraphConfig(framework="langgraph", model="m-1")
    for var, key in LABEL_ENV.items():
        monkeypatch.setenv(var, f"env-{key}")
    assert cfg.labels() == {k: f"env-{k}" for k in LABEL_ENV.values()}


def test_labels_empty_env_falls_back(monkeypatch):
    for var in LABEL_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("POLYGRAPH_FRAMEWORK", "")
    cfg = PolygraphConfig(framework="langgraph")
    labels = cfg.labels()
    assert labels["framework"] == "langgraph"
    assert labels["model"] is None


@pytest.mark.parametrize("name", ["python", "python3", "py", "Python", "python.exe", "python3.exe"])
def test_resolve_command_bare_python(name):
    assert resolve_command([name, "-m", "x"]) == [sys.executable, "-m", "x"]


def test_resolve_command_absolute_path_untouched(tmp_path):
    exe = str(tmp_path / "bin" / "python")
    assert resolve_command([exe, "-m", "x"]) == [exe, "-m", "x"]


def test_resolve_command_other_and_empty():
    cmd = ["node", "server.js"]
    out = resolve_command(cmd)
    assert out == cmd and out is not cmd
    assert resolve_command([]) == []
