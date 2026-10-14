from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select

from conftest import GITHUB_API, GITHUB_WEB, Harness
from polygraph_cloud.config import ConfigError, Settings
from polygraph_cloud.models import GithubCredential, Project, ProjectMember, Session, User


def _repos(*repos: dict) -> dict:
    return {"total_count": len(repos), "repositories": list(repos)}


def _repo(rid: int, full: str, private: bool, perms: dict) -> dict:
    owner, name = full.split("/")
    return {
        "id": rid,
        "name": name,
        "full_name": full,
        "private": private,
        "default_branch": "trunk",
        "owner": {"login": owner},
        "permissions": perms,
    }


def mock_github(gh, *, user_id: int = 5150, login: str = "octo-tester") -> dict:
    calls: dict = {}
    calls["token"] = gh.post(f"{GITHUB_WEB}/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ghu_" + "a" * 36,
                "expires_in": 28800,
                "refresh_token": "ghr_" + "b" * 60,
                "refresh_token_expires_in": 15897600,
                "token_type": "bearer",
            },
        )
    )
    calls["user"] = gh.get(f"{GITHUB_API}/user").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": user_id,
                "login": login,
                "avatar_url": "https://avatars.githubusercontent.com/u/5150",
                "email": "should-never-be-stored@example.test",
                "name": "Should Not Store",
            },
        )
    )
    calls["inst"] = gh.get(f"{GITHUB_API}/user/installations").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "installations": [
                    {"id": 77, "account": {"login": "octo-org", "type": "Organization"}}
                ],
            },
        )
    )
    calls["repos"] = gh.get(f"{GITHUB_API}/user/installations/77/repositories").mock(
        return_value=httpx.Response(
            200,
            json=_repos(
                _repo(
                    9001,
                    "octo-org/private-agent",
                    True,
                    {"admin": False, "maintain": True, "push": True, "pull": True},
                ),
                _repo(
                    9002,
                    "octo-org/open-agent",
                    False,
                    {"admin": False, "maintain": False, "push": True, "pull": True},
                ),
            ),
        )
    )
    return calls


async def _github_login(h: Harness, next_path: str = "/app/octo-org") -> httpx.Response:
    r = await h.client.get("/api/auth/github/login", params={"next": next_path})
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "github.com" and loc.path == "/login/oauth/authorize"
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert "scope" not in q  # never the email scope
    state = q["state"][0]
    return await h.client.get(
        "/api/auth/github/callback", params={"code": "test-code", "state": state}
    )


async def test_github_login_flow_creates_user_session_and_roles(h: Harness, gh) -> None:
    calls = mock_github(gh)
    r = await _github_login(h)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "http://localhost:3000/app/octo-org"
    assert h.client.cookies.get("pg_session") and h.client.cookies.get("pg_csrf")
    set_cookies = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookies if c.startswith("pg_session="))
    assert "HttpOnly" in session_cookie and "SameSite=Lax" in session_cookie
    assert "Max-Age=2592000" in session_cookie

    # PKCE verifier was sent and matches the challenge.
    form = parse_qs(calls["token"].calls[0].request.content.decode())
    assert form["code_verifier"][0] and form["code"] == ["test-code"]

    me = (await h.client.get("/api/me")).json()
    assert me["login"] == "octo-tester" and me["consented_privacy_version"] is None
    assert set(me) == {"id", "login", "avatar_url", "consented_privacy_version", "created_at"}

    async with h.ctx.sessionmaker() as db:
        user = (await db.execute(select(User))).scalar_one()
        cred = await db.get(GithubCredential, user.id)
        assert cred is not None and "ghr_" not in cred.refresh_token_enc  # encrypted at rest
        assert h.ctx.encryptor.decrypt(cred.refresh_token_enc).startswith("ghr_")
        roles = dict(
            (
                await db.execute(
                    select(Project.full_name_lower, ProjectMember.role).join(ProjectMember)
                )
            ).all()
        )
        priv = (
            await db.execute(select(Project).where(Project.github_repo_id == 9001))
        ).scalar_one()
    assert roles == {"octo-org/private-agent": "admin", "octo-org/open-agent": "member"}
    assert priv.is_private and not priv.public_results and priv.default_branch == "trunk"

    lst = (await h.client.get("/api/projects")).json()
    assert [p["full_name"] for p in lst["items"]] == [
        "octo-org/open-agent",
        "octo-org/private-agent",
    ]
    assert {p["full_name"]: p["role"] for p in lst["items"]}["octo-org/private-agent"] == "admin"


async def test_state_mismatch_rejected(h: Harness, gh) -> None:
    mock_github(gh)
    await h.client.get("/api/auth/github/login")
    r = await h.client.get(
        "/api/auth/github/callback", params={"code": "c", "state": "forged-state-value"}
    )
    assert r.status_code == 302
    assert "/signin?error=state_mismatch" in r.headers["location"]
    assert h.client.cookies.get("pg_session") is None


async def test_state_is_single_use(h: Harness, gh) -> None:
    mock_github(gh)
    r = await h.client.get("/api/auth/github/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    ok = await h.client.get("/api/auth/github/callback", params={"code": "c", "state": state})
    assert ok.headers["location"].endswith("/app")
    again = await h.client.get("/api/auth/github/callback", params={"code": "c", "state": state})
    assert "/signin?error=" in again.headers["location"]


@pytest.mark.parametrize("bad", ["//evil.example.test", "https://evil.example.test", "/\\evil"])
async def test_open_redirect_blocked(h: Harness, gh, bad: str) -> None:
    mock_github(gh)
    r = await _github_login(h, next_path=bad)
    assert r.headers["location"] == "http://localhost:3000/app"


async def test_lost_access_removes_membership_on_sync(h: Harness, gh) -> None:
    calls = mock_github(gh)
    await _github_login(h)
    await h.client.post("/api/me/consent", json={"privacy_version": "2026-10-13"}, headers=h.csrf())
    calls["repos"].mock(
        return_value=httpx.Response(
            200, json=_repos(_repo(9002, "octo-org/open-agent", False, {"pull": True}))
        )
    )
    r = await h.client.post("/api/projects/sync", headers=h.csrf())
    assert r.status_code == 200, r.text
    assert [p["full_name"] for p in r.json()["items"]] == ["octo-org/open-agent"]
    # The private project now 404s for this user.
    assert (await h.client.get("/api/projects/octo-org/private-agent")).status_code == 404


async def test_expired_access_token_is_refreshed(h: Harness, gh) -> None:
    from datetime import UTC, datetime, timedelta

    calls = mock_github(gh)
    await _github_login(h)
    async with h.ctx.sessionmaker() as db:
        cred = (await db.execute(select(GithubCredential))).scalar_one()
        cred.access_expires_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()
    await h.client.post("/api/me/consent", json={"privacy_version": "2026-10-13"}, headers=h.csrf())
    before = len(calls["token"].calls)
    assert (await h.client.post("/api/projects/sync", headers=h.csrf())).status_code == 200
    assert len(calls["token"].calls) == before + 1
    form = parse_qs(calls["token"].calls[-1].request.content.decode())
    assert form["grant_type"] == ["refresh_token"]


# --------------------------------------------------------------------------- #
# Sessions, CSRF, consent
# --------------------------------------------------------------------------- #


async def test_csrf_required_for_mutations_with_session(h: Harness) -> None:
    await h.login()
    no = await h.client.post("/api/me/consent", json={"privacy_version": "2026-10-13"})
    assert no.status_code == 403
    assert no.json()["error"]["message"] == "Missing or invalid CSRF token."
    wrong = await h.client.post(
        "/api/me/consent", json={"privacy_version": "2026-10-13"}, headers={"X-CSRF-Token": "nope"}
    )
    assert wrong.status_code == 403
    ok = await h.client.post(
        "/api/me/consent", json={"privacy_version": "2026-10-13"}, headers=h.csrf()
    )
    assert ok.status_code == 200


async def test_csrf_cookie_must_match_session(h: Harness) -> None:
    await h.login("first-user")
    stolen = h.client.cookies.get("pg_csrf")
    async with h.anon() as other:
        await other.post("/api/auth/dev-login", json={"login": "second-user"})
        # Attacker plants their own csrf cookie + header pair next to a victim session.
        other.cookies.set("pg_csrf", stolen)
        r = await other.post("/api/auth/logout", headers={"X-CSRF-Token": stolen})
        assert r.status_code == 403


async def test_foreign_origin_rejected(h: Harness) -> None:
    await h.login()
    r = await h.client.post(
        "/api/auth/logout", headers={**h.csrf(), "Origin": "https://evil.example.test"}
    )
    assert r.status_code == 403


async def test_logout_deletes_session(h: Harness) -> None:
    await h.login()
    r = await h.client.post("/api/auth/logout", headers=h.csrf())
    assert r.status_code == 204
    async with h.ctx.sessionmaker() as db:
        assert (await db.execute(select(func.count()).select_from(Session))).scalar_one() == 0
    h.client.cookies.clear()
    assert (await h.client.get("/api/me")).status_code == 401


async def test_session_stored_hashed(h: Harness) -> None:
    await h.login()
    token = h.client.cookies.get("pg_session")
    async with h.ctx.sessionmaker() as db:
        s = (await db.execute(select(Session))).scalar_one()
    assert s.token_hash != token and len(s.token_hash) == 64


async def test_consent_required_for_changes(h: Harness) -> None:
    await h.login(consent=False)
    r = await h.client.post("/api/projects/sync", headers=h.csrf())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"
    bad = await h.client.post(
        "/api/me/consent", json={"privacy_version": "1999-01-01"}, headers=h.csrf()
    )
    assert bad.status_code == 400


async def test_me_requires_session(h: Harness) -> None:
    r = await h.client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "Sign in first."}}


# --------------------------------------------------------------------------- #
# Environments
# --------------------------------------------------------------------------- #


def _prod_settings(make_settings, **kw) -> Settings:
    from cryptography.fernet import Fernet

    base = dict(
        environment="production",
        public_base_url="https://polygraph.example.test",
        database_url="postgresql+psycopg://app@db.example.test/polygraph",
        storage_backend="s3",
        s3_bucket="polygraph-test-bucket",
        kms_key_id="alias/polygraph",
        queue_backend="sqs",
        sqs_queue_url="https://sqs.ap-south-1.amazonaws.com/0/uploads",
        session_secret="x" * 48,
        token_encryption_key=Fernet.generate_key().decode(),
    )
    base.update(kw)
    return make_settings(**base)


def test_production_refuses_dev_defaults(make_settings) -> None:
    with pytest.raises(ConfigError) as e:
        make_settings(environment="production", database_url="sqlite:///./x.db").check()
    msg = str(e.value)
    for needle in ("SESSION_SECRET", "TOKEN_ENCRYPTION_KEY", "PostgreSQL", "https", "S3", "SQS"):
        assert needle in msg


def test_production_refuses_copied_dev_secret(make_settings) -> None:
    from polygraph_cloud.config import _DEV_SESSION_SECRET

    with pytest.raises(ConfigError, match="development default"):
        _prod_settings(make_settings, session_secret=_DEV_SESSION_SECRET).check()


def test_production_valid_config_passes(make_settings) -> None:
    _prod_settings(make_settings).check()


async def test_dev_login_is_404_in_production(make_settings) -> None:
    from polygraph_cloud.app import create_app

    app = create_app(_prod_settings(make_settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as c:
        r = await c.post("/api/auth/dev-login", json={"login": "dev-user"})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
        assert (await c.get("/api/docs")).status_code == 404
        assert (await c.get("/api/openapi.json")).status_code == 404
        assert "max-age" in r.headers["strict-transport-security"]
    await app.state.ctx.aclose()


async def test_security_headers_and_request_id(h: Harness) -> None:
    r = await h.client.get("/api/health", headers={"X-Request-ID": "trace-12345678"})
    assert r.json() == {"status": "ok"}
    assert r.headers["x-request-id"] == "trace-12345678"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    bad = await h.client.get("/api/health", headers={"X-Request-ID": "<script>"})
    assert bad.headers["x-request-id"] != "<script>"


async def test_cors_locked_to_public_url(h: Harness) -> None:
    ok = await h.client.options(
        "/api/me",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert ok.headers["access-control-allow-credentials"] == "true"
    bad = await h.client.options(
        "/api/me",
        headers={"Origin": "https://evil.example.test", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


async def test_body_limit(make_harness, make_settings) -> None:
    h = await make_harness(make_settings(max_request_bytes=100))
    r = await h.client.post(
        "/api/auth/dev-login",
        content=b"{" + b" " * 500 + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


async def test_unknown_route_uses_envelope(h: Harness) -> None:
    r = await h.client.get("/api/nope")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not found."}}
