"""Tests for polygraph.upload. Everything goes through httpx.MockTransport; no network."""

from __future__ import annotations

import gzip
import logging
import sys
import types
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import polygraph
from polygraph.metrics import summarize, wilson
from polygraph.results import (
    Coverage,
    Environment,
    FaultName,
    FaultSpec,
    Happened,
    LedgerEntry,
    Outcome,
    Payload,
    ResultsFile,
    Run,
    StateCheck,
    iter_payloads,
    parse,
)
from polygraph.upload import (
    DEFAULT_AUDIENCE,
    UploadClient,
    UploadError,
    _redact,
    get_auth_header,
    run_upload,
)

API = "https://api.example.test/api"
TOKEN = "pgp_s3cretUploadToken0042"  # noqa: S105 - fake test token
REQ_TOKEN = "gh-request-token-abcdef123456"  # noqa: S105 - fake test token
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJyZXBvc2l0b3J5IjoiYWNtZS9hZ2VudCJ9.c2lnbmF0dXJl"
OIDC_URL = "https://actions.example.test/_apis/token?api-version=2.0"
TS = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
REPORT = "rep_abc123"
REPORT_URL = f"https://app.example.test/app/acme/agent/reports/{REPORT}"
TOKEN_ENV = {"POLYGRAPH_TOKEN": TOKEN}
OIDC_ENV = {"ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQ_TOKEN}
STATUS_PATH = f"/api/upload/{REPORT}"
COMPARE_PATH = f"/api/upload/{REPORT}/compare"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_results() -> ResultsFile:
    entry = LedgerEntry(
        seq=0,
        ts=TS,
        server="bank",
        method="tools/call",
        tool="pay_invoice",
        fault=FaultName.LOST_RESPONSE,
        asked=Payload.of('{"invoice": "INV-42", "payer": "Customer A"}'),
        happened=Happened(forwarded=2, server_result=Payload.of("paid INV-42")),
        told=None,
    )
    run = Run(
        id="run_0001aaaa",
        test_id="tests/test_bank.py::test_pay",
        task="Pay invoice INV-42",
        fault=FaultSpec(name=FaultName.LOST_RESPONSE, server="bank", tool="pay_invoice"),
        seed=1,
        started_at=TS,
        duration_ms=12.5,
        outcome=Outcome.DOUBLE_ACTION,
        reasons=["pay_invoice forwarded twice"],
        final_answer=Payload.of("Paid INV-42 for Customer A (customer-a@example.test)."),
        state_checks=[StateCheck(name="paid_once", passed=False, detail="2 payments for INV-42")],
        ledger=[entry],
    )
    coverage = Coverage(applicable=1, covered=1)
    return ResultsFile(
        generated_at=TS,
        content_included=True,
        environment=Environment(
            polygraph_version="0.1.0", python_version="3.11.9", platform="linux", seed=1
        ),
        coverage=coverage,
        runs=[run],
        summary=summarize([run], coverage),
    )


@pytest.fixture
def results_path(tmp_path: Path) -> Path:
    p = tmp_path / "results.json"
    p.write_text(make_results().to_json(), encoding="utf-8")
    return p


Responder = httpx.Response | Callable[[httpx.Request], httpx.Response]


class FakeAPI:
    """Scripted server: each (method, path) has a queue of responses; the last one repeats."""

    def __init__(self, routes: dict[tuple[str, str], list[Responder]]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item(request) if callable(item) else item

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]


def accepted() -> httpx.Response:
    return httpx.Response(202, json={"report_id": REPORT, "status": "queued", "url": REPORT_URL})


def status_resp(state: str, reason: str | None = None) -> httpx.Response:
    body = {"report_id": REPORT, "status": state, "rejection_reason": reason}
    return httpx.Response(200, json=body)


def api_error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


def report_lite(rid: str) -> dict[str, Any]:
    r = wilson(0, 1).model_dump()
    return {
        "id": rid,
        "project": "acme/agent",
        "status": "ready",
        "rejection_reason": None,
        "source": "token",
        "branch": "main",
        "sha": "abc1234",
        "pr_number": None,
        "created_at": "2026-10-13T09:30:00Z",
        "framework": None,
        "model": None,
        "config_label": None,
        "runs": 1,
        "faulted_runs": 1,
        "success": r,
        "silent_lie": r,
        "double_action": wilson(1, 1).model_dump(),
        "hijack": r,
        "coverage": {"applicable": 1, "covered": 1},
    }


def compare_body() -> dict[str, Any]:
    r0, r1 = wilson(0, 1).model_dump(), wilson(1, 1).model_dump()
    return {
        "base": report_lite("rep_base1"),
        "head": report_lite(REPORT),
        "metrics": {
            "silent_lie": {"base": r0, "head": r0},
            "double_action": {"base": r0, "head": r1},
            "hijack": {"base": r0, "head": r0},
            "success": {"base": r1, "head": r0},
        },
        "coverage": {
            "base": {"applicable": 1, "covered": 1},
            "head": {"applicable": 1, "covered": 1},
        },
        "pairs": [
            {
                "server": "bank",
                "tool": "pay_invoice",
                "fault": "lost_response",
                "base": {"runs": 1, "bad": 0},
                "head": {"runs": 1, "bad": 1},
            }
        ],
    }


def uploaded(api: FakeAPI) -> ResultsFile:
    (req,) = api.calls("POST", "/api/upload")
    return parse(gzip.decompress(req.content))


def client(api: FakeAPI, **kw: Any) -> UploadClient:
    kw.setdefault("env", TOKEN_ENV)
    return UploadClient(API, transport=api.transport, **kw)


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


def test_token_auth_header() -> None:
    with mock_client(lambda r: httpx.Response(500)) as c:
        assert get_auth_header(c, TOKEN_ENV) == (f"Token {TOKEN}", "token")


@pytest.mark.parametrize(
    "url", [OIDC_URL, "https://actions.example.test/_apis/token"], ids=["query", "noquery"]
)
def test_oidc_flow(url: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": JWT})

    env = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": url,
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQ_TOKEN,
        "POLYGRAPH_TOKEN": TOKEN,  # OIDC wins
    }
    with mock_client(handler) as c:
        assert get_auth_header(c, env) == (f"Bearer {JWT}", "oidc")
    (req,) = seen
    assert req.headers["Authorization"] == f"bearer {REQ_TOKEN}"
    assert req.url.params["audience"] == DEFAULT_AUDIENCE
    assert b"audience=https%3A%2F%2Fpolygraphmcp.dev" in req.url.query
    if "?" in url:
        assert req.url.params["api-version"] == "2.0"
        assert req.url.query.startswith(b"api-version=2.0&audience=")
    assert req.url.path == "/_apis/token"


def test_oidc_custom_audience_env_and_param() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["audience"])
        return httpx.Response(200, json={"value": JWT})

    env = {**OIDC_ENV, "POLYGRAPH_OIDC_AUDIENCE": "https://staging.example.test"}
    with mock_client(handler) as c:
        get_auth_header(c, env)
        get_auth_header(c, env, audience="https://param.example.test")
    assert seen == ["https://staging.example.test", "https://param.example.test"]


def test_no_credentials() -> None:
    with (
        mock_client(lambda r: httpx.Response(500)) as c,
        pytest.raises(UploadError, match="id-token: write.*POLYGRAPH_TOKEN"),
    ):
        get_auth_header(c, {"ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL})


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(403, text=f"denied {REQ_TOKEN}"), "HTTP 403"),
        (httpx.Response(200, json={"nope": 1}), "no token value"),
        (httpx.Response(200, text="not json"), "no token value"),
    ],
)
def test_oidc_failures_do_not_leak(response: httpx.Response, match: str) -> None:
    with (
        mock_client(lambda r: response) as c,
        pytest.raises(UploadError, match=match) as info,
    ):
        get_auth_header(c, OIDC_ENV)
    assert REQ_TOKEN not in str(info.value)


def test_oidc_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with mock_client(handler) as c, pytest.raises(UploadError, match="ConnectError"):
        get_auth_header(c, OIDC_ENV)


def test_upload_with_oidc_fetches_jwt_once() -> None:
    api = FakeAPI(
        {
            ("POST", "/api/upload"): [accepted()],
            ("GET", "/_apis/token"): [httpx.Response(200, json={"value": JWT})],
            ("GET", STATUS_PATH): [status_resp("ready")],
        }
    )
    with client(api, env=OIDC_ENV) as c:
        c.upload(make_results().stripped())
        c.status(REPORT)
        assert c.auth_source == "oidc"
    assert len(api.calls("GET", "/_apis/token")) == 1
    assert api.calls("POST", "/api/upload")[0].headers["Authorization"] == f"Bearer {JWT}"
    assert api.calls("GET", STATUS_PATH)[0].headers["Authorization"] == f"Bearer {JWT}"


# --------------------------------------------------------------------------- #
# Upload body and privacy
# --------------------------------------------------------------------------- #


def test_default_upload_strips_text(results_path: Path) -> None:
    api = FakeAPI({("POST", "/api/upload"): [accepted()]})
    lines: list[str] = []
    rc = run_upload(results_path, api=API, env=TOKEN_ENV, transport=api.transport, out=lines.append)
    assert rc == 0
    (req,) = api.calls("POST", "/api/upload")
    assert req.headers["Content-Encoding"] == "gzip"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["User-Agent"] == f"pytest-polygraph/{polygraph.__version__}"
    assert req.headers["Authorization"] == f"Token {TOKEN}"
    sent = uploaded(api)
    assert sent.content_included is False
    assert all(p.text is None for p in iter_payloads(sent))
    assert sent.runs[0].state_checks[0].detail is None
    assert "Customer A" not in gzip.decompress(req.content).decode()
    assert any(REPORT_URL in line for line in lines)


@pytest.fixture
def mask_module(monkeypatch: pytest.MonkeyPatch) -> str:
    mod = types.ModuleType("pg_test_masks")

    def good(text: str) -> str:
        return text.replace("Customer A", "[NAME]").replace("customer-a@example.test", "[EMAIL]")

    def raises(text: str) -> str:
        raise RuntimeError(f"cannot mask {text}")

    def not_str(text: str) -> Any:
        return None

    mod.good, mod.raises, mod.not_str = good, raises, not_str  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pg_test_masks", mod)
    return "pg_test_masks"


def test_include_content_with_mask_uploads_masked_text(
    results_path: Path, mask_module: str
) -> None:
    api = FakeAPI({("POST", "/api/upload"): [accepted()]})
    rc = run_upload(
        results_path,
        api=API,
        include_content=True,
        mask=f"{mask_module}:good",
        env=TOKEN_ENV,
        transport=api.transport,
        out=lambda s: None,
    )
    assert rc == 0
    sent = uploaded(api)
    assert sent.content_included is True
    assert sent.runs[0].final_answer is not None
    assert sent.runs[0].final_answer.text == "Paid INV-42 for [NAME] ([EMAIL])."
    assert "Customer A" not in sent.to_json()


@pytest.mark.parametrize("attr", ["raises", "not_str", "missing"])
def test_mask_failure_sends_nothing(
    results_path: Path, mask_module: str, attr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeAPI({("POST", "/api/upload"): [accepted()]})
    rc = run_upload(
        results_path,
        api=API,
        include_content=True,
        mask=f"{mask_module}:{attr}",
        env=TOKEN_ENV,
        transport=api.transport,
    )
    assert rc == 2
    assert api.requests == []
    out = capsys.readouterr()
    assert "Nothing was uploaded" in out.out
    assert "Customer A" not in out.out + out.err


def test_include_content_without_mask_refused(results_path: Path) -> None:
    api = FakeAPI({("POST", "/api/upload"): [accepted()]})
    lines: list[str] = []
    rc = run_upload(
        results_path,
        api=API,
        include_content=True,
        env=TOKEN_ENV,
        transport=api.transport,
        out=lines.append,
    )
    assert rc == 2
    assert api.requests == []
    assert "--include-content needs a mask" in lines[0]


def test_bad_input_files(tmp_path: Path) -> None:
    api = FakeAPI({})
    lines: list[str] = []
    missing = tmp_path / "missing.json"
    assert run_upload(missing, env=TOKEN_ENV, transport=api.transport, out=lines.append) == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "polygraph-results/v1", "task": "Customer A"}', encoding="utf-8")
    assert run_upload(bad, env=TOKEN_ENV, transport=api.transport, out=lines.append) == 2
    assert "not a valid polygraph results file" in lines[-1]
    assert "Customer A" not in "\n".join(lines)
    assert api.requests == []


def test_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".polygraph").mkdir()
    (tmp_path / ".polygraph" / "results.json").write_text(make_results().to_json(), "utf-8")
    monkeypatch.chdir(tmp_path)
    api = FakeAPI({("POST", "/api/upload"): [accepted()]})
    assert run_upload(api=API, env=TOKEN_ENV, transport=api.transport, out=lambda s: None) == 0
    assert len(api.calls("POST", "/api/upload")) == 1


# --------------------------------------------------------------------------- #
# Retries and errors
# --------------------------------------------------------------------------- #


def test_retry_on_503_then_202() -> None:
    api = FakeAPI({("POST", "/api/upload"): [httpx.Response(503), accepted()]})
    sleeps: list[float] = []
    with client(api, sleep=sleeps.append) as c:
        info = c.upload(make_results().stripped())
    assert info["report_id"] == REPORT
    assert len(api.calls("POST", "/api/upload")) == 2
    assert len(sleeps) == 1 and 0.5 <= sleeps[0] <= 0.6


def test_backoff_grows_and_gives_up() -> None:
    api = FakeAPI({("POST", "/api/upload"): [httpx.Response(502)]})
    sleeps: list[float] = []
    with (
        client(api, sleep=sleeps.append, max_attempts=4) as c,
        pytest.raises(UploadError, match="HTTP 502") as info,
    ):
        c.upload(make_results().stripped())
    assert info.value.status_code == 502
    assert len(api.calls("POST", "/api/upload")) == 4
    assert 0.5 <= sleeps[0] < 0.6 and 1.0 <= sleeps[1] < 1.1 and 2.0 <= sleeps[2] < 2.1


@pytest.mark.parametrize(("value", "expected"), [("7", 7.0), ("999", 60.0), ("1.5", 1.5)])
def test_retry_after_honoured(value: str, expected: float) -> None:
    first = httpx.Response(429, headers={"Retry-After": value})
    api = FakeAPI({("POST", "/api/upload"): [first, accepted()]})
    sleeps: list[float] = []
    with client(api, sleep=sleeps.append) as c:
        c.upload(make_results().stripped())
    assert sleeps == [expected]


def test_retry_after_http_date_falls_back_to_backoff() -> None:
    first = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    api = FakeAPI({("POST", "/api/upload"): [first, accepted()]})
    sleeps: list[float] = []
    with client(api, sleep=sleeps.append) as c:
        c.upload(make_results().stripped())
    assert len(sleeps) == 1 and 0.5 <= sleeps[0] < 0.6


def test_transport_error_retried_then_fails() -> None:
    script: list[Exception | httpx.Response] = [httpx.ReadTimeout("slow"), accepted()]

    def handler(request: httpx.Request) -> httpx.Response:
        item = script.pop(0) if script else httpx.ConnectError("down")
        if isinstance(item, Exception):
            raise item
        return item

    sleeps: list[float] = []
    transport = httpx.MockTransport(handler)
    with UploadClient(
        API, env=TOKEN_ENV, transport=transport, sleep=sleeps.append, max_attempts=2
    ) as c:
        assert c.upload(make_results().stripped())["report_id"] == REPORT
        with pytest.raises(UploadError, match="could not reach .*ConnectError"):
            c.upload(make_results().stripped())
    assert len(sleeps) == 2


def test_422_rejected_never_leaks_token(
    results_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    message = f"content_included is true; header was Token {TOKEN} {JWT}"
    api = FakeAPI({("POST", "/api/upload"): [api_error(422, "rejected", message)]})

    with client(api) as c, pytest.raises(UploadError) as info:
        c.upload(make_results().stripped())
    err = info.value
    assert err.status_code == 422 and err.code == "rejected"
    assert "rejected" in str(err) and "content_included is true" in str(err)
    assert TOKEN not in str(err) and JWT not in str(err)

    rc = run_upload(results_path, api=API, env=TOKEN_ENV, transport=api.transport)
    assert rc == 1
    captured = capsys.readouterr()
    everything = captured.out + captured.err + caplog.text
    assert "HTTP 422 rejected" in captured.out
    assert TOKEN not in everything
    assert TOKEN[4:] not in everything
    assert JWT not in everything


def test_error_without_json_body() -> None:
    api = FakeAPI({("POST", "/api/upload"): [httpx.Response(400, text="<html>bad</html>")]})
    with (
        client(api) as c,
        pytest.raises(UploadError, match=r"^Upload failed \(HTTP 400\)$"),
    ):
        c.upload(make_results().stripped())


def test_unexpected_success_bodies() -> None:
    bad_id = httpx.Response(202, json={"report_id": "../../etc/passwd", "status": "queued"})
    api = FakeAPI({("POST", "/api/upload"): [httpx.Response(202, text="ok"), bad_id]})
    with client(api) as c:
        with pytest.raises(UploadError, match="unexpected response"):
            c.upload(make_results().stripped())
        with pytest.raises(UploadError, match="Invalid report id"):
            c.upload(make_results().stripped())


def test_redact_helper() -> None:
    text = f"a {TOKEN} b {JWT} c Bearer abcdefghijkl d my-secret-value"
    out = _redact(text, ["my-secret-value", None, ""])
    for s in (TOKEN, JWT, "abcdefghijkl", "my-secret-value"):
        assert s not in out
    assert out.startswith("a [REDACTED] b [REDACTED] c [REDACTED]")


# --------------------------------------------------------------------------- #
# Status, wait, compare
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["../admin", "rep_", "abc", "rep_a/b", "rep_a?x=1"])
def test_invalid_report_id_rejected(bad: str) -> None:
    api = FakeAPI({})
    with client(api) as c:
        for fn in (c.status, c.wait, c.compare):
            with pytest.raises(UploadError, match="Invalid report id"):
                fn(bad)
    assert api.requests == []


def test_wait_polls_until_ready() -> None:
    states = [status_resp("queued"), status_resp("processing"), status_resp("ready")]
    api = FakeAPI({("GET", STATUS_PATH): states})
    sleeps: list[float] = []
    with client(api, sleep=sleeps.append) as c:
        assert c.wait(REPORT, poll_s=2)["status"] == "ready"
    assert sleeps == [2, 2]
    assert len(api.calls("GET", STATUS_PATH)) == 3


def test_wait_returns_rejected() -> None:
    api = FakeAPI({("GET", STATUS_PATH): [status_resp("rejected", "PII found")]})
    with client(api, sleep=lambda s: None) as c:
        assert c.wait(REPORT)["rejection_reason"] == "PII found"


def test_wait_times_out() -> None:
    api = FakeAPI({("GET", STATUS_PATH): [status_resp("processing")]})
    sleeps: list[float] = []
    with (
        client(api, sleep=sleeps.append) as c,
        pytest.raises(UploadError, match="Timed out.*processing"),
    ):
        c.wait(REPORT, timeout_s=6, poll_s=2)
    assert sleeps == [2, 2, 2]


def test_status_error() -> None:
    api = FakeAPI({("GET", STATUS_PATH): [api_error(403, "forbidden", "No access.")]})
    with (
        client(api) as c,
        pytest.raises(UploadError, match=r"Status check failed \(HTTP 403 forbidden\)"),
    ):
        c.status(REPORT)


def test_compare_409_then_200() -> None:
    conflict = api_error(409, "conflict", "processing")
    api = FakeAPI(
        {("GET", COMPARE_PATH): [conflict, conflict, httpx.Response(200, json=compare_body())]}
    )
    sleeps: list[float] = []
    with client(api, sleep=sleeps.append) as c:
        data = c.compare(REPORT, base="branch:release/1.x")
    assert data["head"]["id"] == REPORT
    assert sleeps == [2, 2]
    reqs = api.calls("GET", COMPARE_PATH)
    assert len(reqs) == 3
    assert reqs[0].url.params["base"] == "branch:release/1.x"


def test_compare_times_out() -> None:
    api = FakeAPI({("GET", COMPARE_PATH): [api_error(409, "conflict", "processing")]})
    with (
        client(api, sleep=lambda s: None) as c,
        pytest.raises(UploadError, match="Timed out") as info,
    ):
        c.compare(REPORT, timeout_s=4, poll_s=2)
    assert info.value.status_code == 409


def test_compare_error() -> None:
    api = FakeAPI({("GET", COMPARE_PATH): [api_error(404, "not_found", "No base report.")]})
    with client(api) as c, pytest.raises(UploadError, match="not_found") as info:
        c.compare(REPORT)
    assert info.value.status_code == 404


# --------------------------------------------------------------------------- #
# run_upload end to end
# --------------------------------------------------------------------------- #


def test_run_upload_wait_rejected(results_path: Path) -> None:
    api = FakeAPI(
        {
            ("POST", "/api/upload"): [accepted()],
            ("GET", STATUS_PATH): [
                status_resp("processing"),
                status_resp("rejected", "PII found in text"),
            ],
        }
    )
    lines: list[str] = []
    rc = run_upload(
        results_path,
        api=API,
        wait=True,
        env=TOKEN_ENV,
        transport=api.transport,
        sleep=lambda s: None,
        out=lines.append,
    )
    assert rc == 1
    assert "rejected: PII found in text" in lines[-1]


def test_run_upload_wait_only(results_path: Path) -> None:
    api = FakeAPI(
        {("POST", "/api/upload"): [accepted()], ("GET", STATUS_PATH): [status_resp("ready")]}
    )
    lines: list[str] = []
    rc = run_upload(
        results_path,
        api=API,
        wait=True,
        env=TOKEN_ENV,
        transport=api.transport,
        sleep=lambda s: None,
        out=lines.append,
    )
    assert rc == 0
    assert api.calls("GET", COMPARE_PATH) == []
    assert any("is ready" in line for line in lines)


def test_run_upload_compare_and_pr_comment(results_path: Path, tmp_path: Path) -> None:
    api = FakeAPI(
        {
            ("POST", "/api/upload"): [accepted()],
            ("GET", STATUS_PATH): [status_resp("queued"), status_resp("ready")],
            ("GET", COMPARE_PATH): [
                api_error(409, "conflict", "wait"),
                httpx.Response(200, json=compare_body()),
            ],
        }
    )
    comment = tmp_path / "comment.md"
    rc = run_upload(
        results_path,
        api=API,
        compare_to="main",
        pr_comment=comment,
        env=TOKEN_ENV,
        transport=api.transport,
        sleep=lambda s: None,
        out=lambda s: None,
    )
    assert rc == 0
    md = comment.read_text(encoding="utf-8")
    assert md.startswith("<!-- polygraph -->")
    assert "Customer A" not in md
    assert REPORT_URL in md
    reqs = api.calls("GET", COMPARE_PATH)
    assert len(reqs) == 2
    assert reqs[-1].url.params["base"] == "branch:main"


def test_run_upload_pr_comment_without_base_report(results_path: Path, tmp_path: Path) -> None:
    api = FakeAPI(
        {
            ("POST", "/api/upload"): [accepted()],
            ("GET", STATUS_PATH): [status_resp("ready")],
            ("GET", COMPARE_PATH): [api_error(404, "not_found", "No report on main.")],
        }
    )
    comment = tmp_path / "comment.md"
    lines: list[str] = []
    rc = run_upload(
        results_path,
        api=API,
        compare_to="main",
        pr_comment=comment,
        env=TOKEN_ENV,
        transport=api.transport,
        sleep=lambda s: None,
        out=lines.append,
    )
    assert rc == 0
    assert comment.read_text(encoding="utf-8").startswith("<!-- polygraph -->")
    assert any("No report on 'main'" in line for line in lines)


def test_run_upload_pr_comment_only_waits_without_compare(
    results_path: Path, tmp_path: Path
) -> None:
    api = FakeAPI(
        {("POST", "/api/upload"): [accepted()], ("GET", STATUS_PATH): [status_resp("ready")]}
    )
    comment = tmp_path / "comment.md"
    rc = run_upload(
        results_path,
        api=API,
        pr_comment=comment,
        env=TOKEN_ENV,
        transport=api.transport,
        sleep=lambda s: None,
        out=lambda s: None,
    )
    assert rc == 0
    assert api.calls("GET", COMPARE_PATH) == []
    assert comment.read_text(encoding="utf-8").startswith("<!-- polygraph -->")


def test_run_upload_compare_other_error(results_path: Path) -> None:
    api = FakeAPI(
        {
            ("POST", "/api/upload"): [accepted()],
            ("GET", STATUS_PATH): [status_resp("ready")],
            ("GET", COMPARE_PATH): [api_error(403, "forbidden", "No.")],
        }
    )
    rc = run_upload(
        results_path,
        api=API,
        compare_to="main",
        env=TOKEN_ENV,
        transport=api.transport,
        sleep=lambda s: None,
        out=lambda s: None,
    )
    assert rc == 1


def test_run_upload_no_credentials(results_path: Path) -> None:
    api = FakeAPI({("POST", "/api/upload"): [accepted()]})
    lines: list[str] = []
    rc = run_upload(results_path, api=API, env={}, transport=api.transport, out=lines.append)
    assert rc == 1
    assert api.requests == []
    assert "No upload credentials" in lines[-1]


def test_compare_409_honours_retry_after() -> None:
    conflict = httpx.Response(
        409,
        json={"error": {"code": "conflict", "message": "processing"}},
        headers={"Retry-After": "5"},
    )
    api = FakeAPI({("GET", COMPARE_PATH): [conflict, httpx.Response(200, json=compare_body())]})
    sleeps: list[float] = []
    with client(api, sleep=sleeps.append) as c:
        c.compare(REPORT)
    assert sleeps == [5.0]


def test_consent_required_is_a_clear_error() -> None:
    api = FakeAPI(
        {("GET", STATUS_PATH): [api_error(403, "consent_required", "Accept the privacy notice.")]}
    )
    with client(api) as c, pytest.raises(UploadError, match="consent_required") as info:
        c.status(REPORT)
    assert info.value.status_code == 403 and info.value.code == "consent_required"
