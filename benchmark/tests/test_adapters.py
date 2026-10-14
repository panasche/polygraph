"""Adapter import and smoke tests.

The real-framework smoke tests skip when that framework's extra is not installed.
When it is, they start the bank world behind the Polygraph proxy and let the
framework's own MCP client list the tools and make one call, with no model and
no network, then check the protocol version the proxy recorded.
"""

from __future__ import annotations

import importlib

import pytest

import adapters
from adapters import ADAPTERS, FRAMEWORKS, SCRIPTED, NotInstalled, adapter_class, build_agent
from bench.models import ModelSpec
from bench.probe import probe
from bench.prompt import SYSTEM_PROMPT

#: what each framework was seen to speak against the worlds with its extra installed alone
#: (see README). LangGraph and smolagents need mcp<2; the other two get mcp 2.x.
EXPECTED_PROTOCOL = {
    "langgraph": "2025-11-25",
    "openai-agents": "2026-07-28",
    "pydantic-ai": "2026-07-28",
    "smolagents": "2025-11-25",
    "scripted-naive": "2026-07-28",
    "scripted-careful": "2026-07-28",
}


def expected_protocol(framework: str) -> str:
    """The version above, or 2025-11-25 when a shared venv pulled the MCP SDK below 2.x."""
    if framework.startswith("scripted"):
        return EXPECTED_PROTOCOL[framework]
    import importlib.metadata

    major = int(importlib.metadata.version("mcp").split(".")[0])
    return EXPECTED_PROTOCOL[framework] if major >= 2 else "2025-11-25"


@pytest.mark.parametrize("framework", list(ADAPTERS))
def test_adapter_modules_import_without_their_framework(framework: str) -> None:
    cls = adapter_class(framework)
    assert cls.framework == framework
    assert cls.distribution and cls.extra
    assert isinstance(cls.BEST_PRACTICE, dict)
    importlib.import_module(ADAPTERS[framework].partition(":")[0])


def test_best_practice_is_empty_until_maintainers_suggest_settings() -> None:
    for framework in FRAMEWORKS:
        assert adapter_class(framework).BEST_PRACTICE == {}, framework


def test_unknown_framework() -> None:
    with pytest.raises(ValueError, match="unknown framework"):
        adapter_class("crewai")


def test_build_agent_enforces_the_fairness_rules() -> None:
    servers = {"bank": {"command": ["python", "-m", "polygraph.worlds.bank"]}}
    with pytest.raises(ValueError, match="shared"):
        build_agent(servers, "scripted", "Be extra careful.", framework="scripted-naive")
    with pytest.raises(ValueError, match="model 'scripted'"):
        build_agent(servers, "openai:some-model", SYSTEM_PROMPT, framework="scripted-naive")
    with pytest.raises(ValueError, match="real model"):
        build_agent(servers, "scripted", SYSTEM_PROMPT, framework="langgraph")
    with pytest.raises(ValueError, match="unknown config label"):
        build_agent(servers, "scripted", SYSTEM_PROMPT, "R9", framework="scripted-naive")


def test_recipe_prompt_lines_are_appended_to_the_shared_prompt() -> None:
    servers = {"bank": {"command": ["python"]}}
    agent = build_agent(servers, "scripted", SYSTEM_PROMPT, "R5+R4+R2", framework="scripted-naive")
    prompt = agent.system_prompt  # type: ignore[attr-defined]
    assert prompt.startswith(SYSTEM_PROMPT)
    assert prompt.index("read the state back") < prompt.index("data, not instructions")
    assert agent.config_label == "R2+R4+R5"
    plain = build_agent(servers, "scripted", SYSTEM_PROMPT, framework="scripted-naive")
    assert plain.system_prompt == SYSTEM_PROMPT  # type: ignore[attr-defined]


def test_missing_framework_raises_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    cls = adapter_class("smolagents")
    monkeypatch.setattr(cls, "requires", ("polygraph_bench_module_that_does_not_exist",))
    with pytest.raises(NotInstalled, match="pip install"):
        cls({}, ModelSpec("openai", "m"), SYSTEM_PROMPT)


def test_stdio_parts_keeps_what_python_needs() -> None:
    command, args, env, cwd = adapters.BaseAgent.stdio_parts(
        {"command": ["py", "-m", "x"], "env": {"POLYGRAPH_RUN_DIR": "d"}, "cwd": "c"}
    )
    assert (command, args, cwd) == ("py", ["-m", "x"], "c")
    assert env["POLYGRAPH_RUN_DIR"] == "d"


def _framework_params() -> list[object]:
    out: list[object] = []
    for f in ADAPTERS:
        cls = adapter_class(f)
        out.append(
            pytest.param(
                f,
                marks=pytest.mark.skipif(
                    not cls.available(),
                    reason=f'{f} not installed (pip install -e ".[{cls.extra}]")',
                ),
            )
        )
    return out


@pytest.mark.e2e
@pytest.mark.parametrize("framework", _framework_params())
@pytest.mark.parametrize("config_label", ["default", "all-recipes"])
def test_framework_mcp_client_through_the_proxy(framework: str, config_label: str) -> None:
    result = probe(framework, config_label=config_label)
    assert result.error is None, result.error
    assert result.tools == ["get_invoice", "pay_invoice", "refund"]
    assert "tools/call" in result.methods
    assert result.protocol_versions == [expected_protocol(framework)]
    assert result.framework_version


def test_scripted_adapters_are_always_available() -> None:
    for f in SCRIPTED:
        assert adapter_class(f).available()
