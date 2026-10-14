"""Fixtures for the bank example: one agent fixture, run once per agent kind.

The naive agent is here to show what goes wrong, so its runs are scored but never
fail the suite. The careful agent is the regression gate: any bad outcome fails.
"""

from __future__ import annotations

import pytest

from polygraph import Polygraph
from polygraph.testing import CarefulAgent, NaiveAgent, ScriptedAgent


@pytest.fixture(params=["naive", "careful"])
def agent(request: pytest.FixtureRequest, polygraph: Polygraph) -> ScriptedAgent:
    """A scripted agent wired to the servers Polygraph hands out (through the proxy)."""
    if request.param == "naive":
        return NaiveAgent(polygraph.servers(), call_timeout=10)
    return CarefulAgent(polygraph.servers(), call_timeout=10, seed=polygraph.seed)


def verdict(agent: ScriptedAgent, polygraph: Polygraph, answer: str) -> None:
    """Score the run; fail only for the careful agent (with one known server bug)."""
    outcome = polygraph.score(answer)
    if agent.name == "naive":
        return
    fault = polygraph.fault
    if (
        fault is not None
        and fault.tool == "refund"
        and fault.name.value == "duplicate_delivery"
        and outcome.value == "double_action"
    ):
        pytest.xfail("refund claims to be safe to repeat but isn't: a server bug Polygraph catches")
    polygraph.assert_honest(answer)
