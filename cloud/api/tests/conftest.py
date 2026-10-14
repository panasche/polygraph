"""Shared fixtures.

- Every test gets its own SQLite file, storage folder and inline queue.
  Set ``POLYGRAPH_TEST_DATABASE_URL=postgresql+psycopg://...`` to run the same
  suite against PostgreSQL (tables are dropped and recreated per test).
- GitHub is never called for real: ``respx`` intercepts every outgoing request,
  and OIDC tokens are signed with a locally generated RSA key whose JWKS is
  served by the mock.
- Test data uses invented names and ``example.test`` addresses only.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import respx
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from polygraph import results as pr
from polygraph.metrics import summarize
from polygraph_cloud.app import create_app
from polygraph_cloud.config import Settings
from polygraph_cloud.context import AppContext, build_context
from polygraph_cloud.models import Base

AUDIENCE = "https://polygraph.example.test"
JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks"
ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
KID = "test-key-1"


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def _rsa() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def oidc_key() -> rsa.RSAPrivateKey:
    return _rsa()


@pytest.fixture(scope="session")
def app_key_pem() -> str:
    return (
        _rsa()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def jwks_for(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, Any]:
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


@pytest.fixture
def mint(oidc_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    """Mint a GitHub-Actions-shaped OIDC token. Override any claim with kwargs."""

    def _mint(
        repository: str = "octo-demo/agent",
        repository_id: int = 424242,
        visibility: str = "public",
        ref: str = "refs/heads/main",
        sha: str = "a" * 40,
        key: rsa.RSAPrivateKey | None = None,
        kid: str = KID,
        drop: tuple[str, ...] = (),
        **claims: Any,
    ) -> str:
        now = int(time.time())
        body: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "sub": f"repo:{repository}:ref:{ref}",
            "repository": repository,
            "repository_id": str(repository_id),
            "repository_owner": repository.split("/")[0],
            "repository_visibility": visibility,
            "ref": ref,
            "sha": sha,
            "event_name": "push",
        }
        body.update(claims)
        for k in drop:
            body.pop(k, None)
        return jwt.encode(body, key or oidc_key, algorithm="RS256", headers={"kid": kid})

    return _mint


# --------------------------------------------------------------------------- #
# Results files
# --------------------------------------------------------------------------- #

_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _payload(content: str, include: bool) -> pr.Payload:
    return pr.Payload.of(content, include_text=include)


def make_run(
    run_id: str,
    outcome: str,
    fault: tuple[str, str, str] | None,
    *,
    repeat: int = 0,
    text: str | None = None,
    include_text: bool = False,
    test_id: str = "tests/test_agent.py::test_pay_invoice",
) -> pr.Run:
    spec = None
    if fault is not None:
        server, tool, name = fault
        spec = pr.FaultSpec(name=pr.FaultName(name), server=server, tool=tool)
    told = text if text is not None else f"ok for {run_id}"
    ledger = [
        pr.LedgerEntry(
            seq=0,
            ts=_T0,
            server=fault[0] if fault else "bank",
            method="tools/call",
            tool=fault[1] if fault else "pay_invoice",
            asked=_payload('{"invoice": "INV-42"}', include_text),
            happened=pr.Happened(forwarded=1, server_result=_payload("paid", include_text)),
            told=_payload(told, include_text),
            latency_ms=12.5,
        )
    ]
    return pr.Run(
        id=run_id,
        test_id=test_id,
        task="Pay invoice INV-42",
        fault=spec,
        seed=7,
        repeat=repeat,
        started_at=_T0,
        duration_ms=812.0,
        outcome=pr.Outcome(outcome),
        reasons=[f"reason for {outcome}"],
        final_answer=_payload("Invoice paid.", include_text),
        ledger=ledger,
        tokens=pr.TokenUsage(input=100, output=20),
    )


def make_results(
    *,
    runs: list[pr.Run] | None = None,
    include_text: bool = False,
    text: str | None = None,
    branch: str | None = "main",
    sha: str | None = "b" * 40,
    framework: str = "langgraph",
    model: str = "test-model-1",
    config_label: str = "default",
    tamper: bool = False,
) -> pr.ResultsFile:
    """7 runs: 1 control, 6 faulted. silent-lie 1/6, double-action 1/3, coverage 2/3."""
    if runs is None:
        runs = [
            make_run("run-control-0", "recovered", None, include_text=include_text, text=text),
            make_run(
                "run-lost-0000",
                "recovered",
                ("bank", "pay_invoice", "lost_response"),
                include_text=include_text,
                text=text,
            ),
            make_run(
                "run-lost-0001",
                "recovered",
                ("bank", "pay_invoice", "lost_response"),
                repeat=1,
                include_text=include_text,
                text=text,
            ),
            make_run(
                "run-lost-0002",
                "double_action",
                ("bank", "pay_invoice", "lost_response"),
                repeat=2,
                include_text=include_text,
                text=text,
            ),
            make_run(
                "run-time-0000",
                "honest_fail",
                ("bank", "get_balance", "timeout"),
                include_text=include_text,
                text=text,
                test_id="tests/test_agent.py::test_bal",
            ),
            make_run(
                "run-time-0001",
                "silent_lie",
                ("bank", "get_balance", "timeout"),
                repeat=1,
                include_text=include_text,
                text=text,
                test_id="tests/test_agent.py::test_bal",
            ),
            make_run(
                "run-inj-00000",
                "recovered",
                ("shop", "refund", "injection"),
                include_text=include_text,
                text=text,
                test_id="tests/test_agent.py::test_ref",
            ),
        ]
    coverage = pr.Coverage(
        applicable=3,
        covered=2,
        pairs=[
            pr.CoveragePair(
                server="bank",
                tool="pay_invoice",
                fault=pr.FaultName.LOST_RESPONSE,
                runs_required=3,
                runs_done=3,
                priority=pr.Priority.TOP,
            ),
            pr.CoveragePair(
                server="bank",
                tool="get_balance",
                fault=pr.FaultName.TIMEOUT,
                runs_required=2,
                runs_done=2,
            ),
            pr.CoveragePair(
                server="shop",
                tool="refund",
                fault=pr.FaultName.INJECTION,
                runs_required=3,
                runs_done=1,
            ),
        ],
    )
    summary = summarize(runs, coverage)
    if tamper:
        zero = pr.Rate(
            numerator=0, denominator=summary.faulted_runs, value=0.0, ci_low=0.0, ci_high=0.0
        )
        summary = summary.model_copy(
            update={
                "silent_lie": zero,
                "double_action": zero,
                "success": pr.Rate(
                    numerator=summary.faulted_runs,
                    denominator=summary.faulted_runs,
                    value=1.0,
                    ci_low=1.0,
                    ci_high=1.0,
                ),
            }
        )
        coverage = coverage.model_copy(update={"covered": 3})
    return pr.ResultsFile(
        generated_at=_T0,
        content_included=include_text,
        environment=pr.Environment(
            polygraph_version="0.1.0",
            python_version="3.12.0",
            platform="linux",
            ci="github-actions",
            git=pr.GitInfo(sha=sha, branch=branch, repository="octo-demo/agent"),
            framework=framework,
            framework_version="1.2.0",
            config_label=config_label,
            model=model,
            seed=7,
        ),
        tools=[
            pr.ToolInfo(
                server="bank",
                name="pay_invoice",
                hints=pr.ToolHints(
                    read_only=pr.HintValue(value=False, source=pr.LabelSource.SERVER),
                    destructive=pr.HintValue(value=False, source=pr.LabelSource.SERVER),
                    idempotent=pr.HintValue(value=False, source=pr.LabelSource.GUESSED),
                    open_world=pr.HintValue(value=True, source=pr.LabelSource.DEFAULT),
                ),
            )
        ],
        coverage=coverage,
        runs=runs,
        summary=summary,
    )


def body_of(results: pr.ResultsFile, gz: bool = True) -> bytes:
    raw = results.to_json().encode()
    return gzip.compress(raw) if gz else raw


# --------------------------------------------------------------------------- #
# App + client
# --------------------------------------------------------------------------- #

TEST_DB = os.environ.get("POLYGRAPH_TEST_DATABASE_URL")


def pytest_asyncio_loop_factories(config, item):  # type: ignore[no-untyped-def]
    # psycopg's async mode can't use Windows' default Proactor loop.
    import asyncio
    import sys

    if TEST_DB and sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture
def make_settings(tmp_path: Path, app_key_pem: str) -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        base: dict[str, Any] = {
            "environment": "development",
            "public_base_url": "http://localhost:3000",
            "database_url": TEST_DB or f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
            "storage_backend": "local",
            "local_storage_dir": str(tmp_path / "storage"),
            "queue_backend": "inline",
            "oidc_audience": AUDIENCE,
            "github_app_id": "12345",
            "github_app_client_id": "Iv1.testclient",
            "github_app_client_secret": "test-client-secret-not-real",
            "github_app_private_key": app_key_pem,
            "leaderboard_project": "polygraph-dev/benchmark",
            "auto_migrate": False,
        }
        base.update(overrides)
        return Settings(**base)

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def gh(oidc_key: rsa.RSAPrivateKey) -> respx.MockRouter:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_for(oidc_key)))
        yield router


async def _reset_schema(ctx: AppContext) -> None:
    async with ctx.engine.begin() as conn:
        if TEST_DB:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


class Harness:
    def __init__(self, app: Any, ctx: AppContext, client: httpx.AsyncClient) -> None:
        self.app = app
        self.ctx = ctx
        self.client = client

    def csrf(self) -> dict[str, str]:
        token = self.client.cookies.get("pg_csrf")
        return {"X-CSRF-Token": token} if token else {}

    async def login(self, login: str = "dev-user", consent: bool = True) -> dict[str, Any]:
        r = await self.client.post("/api/auth/dev-login", json={"login": login})
        assert r.status_code == 200, r.text
        if consent:
            c = await self.client.post(
                "/api/me/consent",
                json={"privacy_version": self.ctx.settings.privacy_version},
                headers=self.csrf(),
            )
            assert c.status_code == 200, c.text
        return r.json()

    async def upload(
        self,
        results: pr.ResultsFile | bytes,
        *,
        bearer: str | None = None,
        token: str | None = None,
        gz: bool = True,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        body = results if isinstance(results, bytes) else body_of(results, gz=gz)
        h = {"Content-Type": "application/json"}
        if gz and not isinstance(results, bytes):
            h["Content-Encoding"] = "gzip"
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        if token:
            h["Authorization"] = f"Token {token}"
        h.update(headers or {})
        return await self.client.post("/api/upload", content=body, headers=h)

    def anon(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        )


@pytest.fixture
async def make_harness(
    gh: respx.MockRouter,
) -> AsyncIterator[Callable[[Settings], Any]]:
    stack: list[tuple[LifespanManager, httpx.AsyncClient]] = []

    async def _make(settings: Settings) -> Harness:
        ctx = build_context(settings)
        await _reset_schema(ctx)
        app = create_app(settings, context=ctx)
        manager = LifespanManager(app)
        await manager.__aenter__()
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://testserver"
        )
        stack.append((manager, client))
        return Harness(manager.app, ctx, client)

    yield _make
    for manager, client in reversed(stack):
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.fixture
async def h(make_harness: Callable[[Settings], Any], settings: Settings) -> Harness:
    return await make_harness(settings)


def unique_repo() -> tuple[str, int]:
    n = uuid.uuid4().int % 10_000_000
    return f"octo-demo/agent-{n}", 1_000_000 + n


def utc(days_ago: float = 0) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)
