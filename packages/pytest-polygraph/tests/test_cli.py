"""The ``polygraph`` command line."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from samples import write_sample

from polygraph import cli
from polygraph.results import parse


@pytest.fixture
def results_file(tmp_path):
    return write_sample(tmp_path / ".polygraph" / "results.json")


def test_validate_ok(results_file, capsys):
    assert cli.main(["validate", str(results_file)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ok: 2 runs (1 with a fault)")
    assert "content included: true" in out


def test_validate_bad_schema_does_not_echo_payload(tmp_path, capsys):
    data = json.loads(write_sample(tmp_path / "r.json").read_text(encoding="utf-8"))
    data["runs"][0]["outcome"] = "SECRET-PAYLOAD-TEXT"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    assert cli.main(["validate", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "runs.0.outcome" in err and "SECRET-PAYLOAD-TEXT" not in err


def test_validate_not_json_and_missing(tmp_path, capsys):
    p = tmp_path / "x.json"
    p.write_text("{not json", encoding="utf-8")
    assert cli.main(["validate", str(p)]) == 1
    assert cli.main(["validate", str(tmp_path / "missing.json")]) == 2


def test_schema(capsys):
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "schema" in schema["properties"]


def test_report_html_junit_and_pr_comment(results_file, tmp_path, capsys):
    base = write_sample(tmp_path / "base.json", bad=False)
    html, junit, md = tmp_path / "o.html", tmp_path / "o.xml", tmp_path / "o.md"
    rc = cli.main(
        [
            "report",
            "--results",
            str(results_file),
            "--html",
            str(html),
            "--junit",
            str(junit),
            "--pr-comment",
            str(md),
            "--compare",
            str(base),
            "--base-label",
            "main",
        ]
    )
    assert rc == 0
    assert "Asked" in html.read_text(encoding="utf-8")
    ET.fromstring(junit.read_text(encoding="utf-8"))
    comment = md.read_text(encoding="utf-8")
    assert comment.startswith("<!-- polygraph -->")
    assert "vs `main`" in comment


def test_report_with_cloud_compare_json(results_file, tmp_path):
    rate = {"numerator": 0, "denominator": 10, "value": 0.0, "ci_low": 0.0, "ci_high": 0.3}
    lite = {
        "branch": "main",
        "success": rate,
        "silent_lie": rate,
        "double_action": rate,
        "hijack": rate,
        "coverage": {"applicable": 30, "covered": 3},
    }
    compare = {"base": lite, "head": lite, "metrics": {}, "coverage": {}, "pairs": []}
    cmp_path = tmp_path / "cmp.json"
    cmp_path.write_text(json.dumps(compare), encoding="utf-8")
    md = tmp_path / "c.md"
    assert (
        cli.main(
            [
                "report",
                "--results",
                str(results_file),
                "--pr-comment",
                str(md),
                "--compare",
                str(cmp_path),
            ]
        )
        == 0
    )
    assert md.read_text(encoding="utf-8").startswith("<!-- polygraph -->")
    junk = tmp_path / "junk.json"
    junk.write_text("[]", encoding="utf-8")
    assert (
        cli.main(
            [
                "report",
                "--results",
                str(results_file),
                "--pr-comment",
                str(md),
                "--compare",
                str(junk),
            ]
        )
        == 2
    )


def test_report_needs_an_output(results_file):
    assert cli.main(["report", "--results", str(results_file)]) == 2


def test_badge(results_file, tmp_path, capsys):
    out = tmp_path / "badge.svg"
    assert cli.main(["badge", "--results", str(results_file), "--out", str(out)]) == 0
    svg = out.read_text(encoding="utf-8")
    ET.fromstring(svg)
    assert "silent-lie" in svg and "coverage" in svg


def test_plan_text_and_json(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.polygraph.servers]\n"
        'bank = { command = ["python", "-m", "polygraph.worlds.bank"] }\n'
        "[tool.polygraph.overrides]\n"
        "refund = { idempotent = false }\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert cli.main(["plan"]) == 0
    out = capsys.readouterr().out
    assert "bank/pay_invoice" in out and "(guessed)" in out and "(override)" in out
    assert "claims to check: idempotent" in out
    assert "applicable tool × fault pairs" in out
    assert cli.main(["plan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["claims"] == {"bank/refund": ["idempotent"]}
    assert {p["fault"] for p in data["pairs"]} >= {"lost_response", "description_poison"}


def test_plan_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.polygraph]\n", encoding="utf-8")
    assert cli.main(["plan"]) == 2
    (tmp_path / "pyproject.toml").write_text("[tool.polygraph]\nruns = 0\n", encoding="utf-8")
    assert cli.main(["plan"]) == 2
    (tmp_path / "pyproject.toml").write_text(
        '[tool.polygraph.servers]\nbroken = { command = ["python", "-c", "import sys"] }\n',
        encoding="utf-8",
    )
    assert cli.main(["plan", "--refresh"]) == 1
    assert "could not list tools" in capsys.readouterr().err


def test_proxy_argument_errors(tmp_path, capsys):
    assert cli.main(["proxy", "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert cli.main(["proxy", "--listen", "127.0.0.1:0"]) == 2
    bad = tmp_path / "plan.json"
    bad.write_text("{ nope", encoding="utf-8")
    assert cli.main(["proxy", "--fault-plan", str(bad), "--", "python", "-V"]) == 2


def test_load_plan_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYGRAPH_FAULT_PLAN", raising=False)
    assert cli._load_plan(None).faults == []
    monkeypatch.setenv("POLYGRAPH_FAULT_PLAN", '{"server": "bank", "seed": 3}')
    assert cli._load_plan(None).seed == 3
    f = tmp_path / "p.json"
    f.write_text('{"server": "shop"}', encoding="utf-8")
    assert cli._load_plan(str(f)).server == "shop"


def test_upload_wires_config_mask(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.polygraph]\nmask = "mymask:mask"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake(path, **kw):
        seen.update(kw, path=path)
        return 0

    monkeypatch.setattr("polygraph.upload.run_upload", fake)
    assert (
        cli.main(
            [
                "upload",
                "r.json",
                "--include-content",
                "--wait",
                "--compare-to",
                "main",
                "--pr-comment",
                "c.md",
            ]
        )
        == 0
    )
    assert seen["mask"] == "mymask:mask" and seen["include_content"] is True
    assert seen["compare_to"] == "main" and seen["path"] == "r.json"
    (tmp_path / "pyproject.toml").write_text("[tool.polygraph]\nruns = 0\n", encoding="utf-8")
    assert cli.main(["upload"]) == 2


def test_replay_missing_cassette(tmp_path, capsys):
    assert cli.main(["replay", "run_doesnotexist", "--root", str(tmp_path)]) == 2


@pytest.mark.integration
def test_python_dash_m_polygraph_runs(results_file):
    out = subprocess.run(
        [sys.executable, "-m", "polygraph", "validate", str(results_file)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0 and out.stdout.startswith("ok:")
    out = subprocess.run(
        [sys.executable, "-m", "polygraph", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "polygraph" in out.stdout


@pytest.mark.integration
def test_http_proxy_cli_listens(tmp_path):
    """``polygraph proxy --listen`` prints its URL and forwards to the upstream."""
    import httpx
    from helpers import build_world

    from polygraph.proxy.http import ServerThread

    server, _ = build_world("bank", tmp_path / "bank.sqlite")
    with ServerThread(server.asgi_app()) as upstream:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "polygraph",
                "proxy",
                "--ledger",
                str(tmp_path / "l.jsonl"),
                "--listen",
                "127.0.0.1:0",
                "--upstream",
                upstream.url,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            line = proc.stdout.readline()  # type: ignore[union-attr]
            url = line.split(" on ")[1].split(" ")[0]
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
            }
            r = httpx.post(
                url,
                json=body,
                headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"},
                timeout=20,
            )
            assert r.status_code == 200 and "get_invoice" in r.text
        finally:
            proc.terminate()
            proc.wait(timeout=20)
    assert "tools/list" in (tmp_path / "l.jsonl").read_text(encoding="utf-8")


def test_sample_results_round_trip(results_file):
    assert parse(results_file.read_text(encoding="utf-8")).summary.runs == 2
