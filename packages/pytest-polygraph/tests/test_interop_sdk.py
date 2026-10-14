"""Compatibility with the official MCP Python SDK client (``mcp`` 2.x).

The SDK client negotiates the era itself (it probes with ``server/discover`` and
uses 2026-07-28 against our dual-era worlds). These tests check that it can use
the worlds directly, through the stdio proxy and through the HTTP proxy, and
that the proxy's ledger records what it said.
"""

from __future__ import annotations

import sys

import anyio
import pytest
from helpers import http_proxy, http_world, world_cmd

from polygraph.faults import FaultPlan
from polygraph.ledger import read_ledger

mcp = pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

pytestmark = pytest.mark.integration


async def exercise(target) -> tuple[list[str], dict]:
    with anyio.fail_after(60):
        async with Client(target) as client:
            listed = await client.list_tools()
            names = [t.name for t in listed.tools]
            inv = await client.call_tool("get_invoice", {"invoice_id": "INV-42"})
            pay = await client.call_tool("pay_invoice", {"invoice_id": "INV-42"})
            assert not inv.is_error and not pay.is_error
            return names, inv.structured_content or {}


@pytest.mark.anyio
async def test_sdk_client_talks_to_a_world_over_stdio(tmp_path):
    cmd = world_cmd("bank", "--db", str(tmp_path / "b.sqlite"), "--reset")
    names, inv = await exercise(StdioServerParameters(command=cmd[0], args=cmd[1:]))
    assert names == ["get_invoice", "pay_invoice", "refund"]
    assert inv["status"] == "open"


@pytest.mark.anyio
async def test_sdk_client_through_the_stdio_proxy(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(FaultPlan(server="bank").model_dump_json(), encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    args = [
        "-m",
        "polygraph",
        "proxy",
        "--fault-plan",
        str(plan),
        "--ledger",
        str(ledger),
        "--",
        *world_cmd("bank", "--db", str(tmp_path / "b.sqlite"), "--reset"),
    ]
    names, _ = await exercise(StdioServerParameters(command=sys.executable, args=args))
    assert "pay_invoice" in names
    recs = read_ledger(ledger)
    calls = [r for r in recs if r.entry.method == "tools/call"]
    assert [c.entry.tool for c in calls] == ["get_invoice", "pay_invoice"]
    assert all(c.entry.protocol_version for c in calls)


@pytest.mark.anyio
async def test_sdk_client_through_the_http_proxy(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    with (
        http_world("bank", tmp_path / "b.sqlite") as (up, db),
        http_proxy(FaultPlan(server="bank"), ledger, up) as url,
    ):
        names, _ = await exercise(url)
        assert db.payments_for("INV-42") == 1
    assert "refund" in names
    versions = {
        r.entry.protocol_version for r in read_ledger(ledger) if r.entry.method == "tools/call"
    }
    assert versions and None not in versions
