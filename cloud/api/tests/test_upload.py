from __future__ import annotations

import gzip
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from conftest import (
    AUDIENCE,
    GITHUB_API,
    ISSUER,
    Harness,
    body_of,
    make_results,
    make_run,
    unique_repo,
)
from polygraph_cloud.models import Project, Report
from polygraph_cloud.storage import raw_key, run_key


async def _report(h: Harness, report_id: str) -> Report:
    async with h.ctx.sessionmaker() as db:
        r = await db.get(Report, report_id)
        assert r is not None
        return r


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


async def test_oidc_upload_creates_public_project_and_processes(h: Harness, mint) -> None:
    repo, rid = unique_repo()
    token = mint(repository=repo, repository_id=rid, ref="refs/heads/main", sha="c" * 40)
    # The file claims another branch and sha: the signed token wins.
    r = await h.upload(make_results(branch="feature-x", sha="d" * 40), bearer=token)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["report_id"].startswith("rep_")
    assert body["url"] == f"http://localhost:3000/app/{repo}/reports/{body['report_id']}"

    rep = await _report(h, body["report_id"])
    assert rep.status == "ready"
    assert rep.source == "oidc"
    assert rep.branch == "main" and rep.sha == "c" * 40
    assert (rep.silent_lie_num, rep.silent_lie_den) == (1, 6)
    assert (rep.double_action_num, rep.double_action_den) == (1, 3)
    assert (rep.coverage_covered, rep.coverage_applicable) == (2, 3)

    async with h.ctx.sessionmaker() as db:
        project = (
            await db.execute(select(Project).where(Project.github_repo_id == rid))
        ).scalar_one()
    assert project.is_private is False and project.public_results is True
    assert await h.ctx.storage.get(raw_key(project.id, rep.id)) is not None
    assert await h.ctx.storage.get(run_key(project.id, rep.id, "run-lost-0002")) is not None

    # Anyone can read a public project's report.
    async with h.anon() as anon:
        d = await anon.get(f"/api/projects/{repo}/reports/{rep.id}")
        assert d.status_code == 200
        detail = d.json()
        assert detail["runs"] == 7
        assert detail["run_list"][0]["outcome"] in {"double_action", "silent_lie"}
        assert len(detail["run_list"]) == 7

    s = await h.client.get(f"/api/upload/{rep.id}", headers={"Authorization": f"Bearer {token}"})
    assert s.json() == {"report_id": rep.id, "status": "ready", "rejection_reason": None}


async def test_oidc_pull_request_ref(h: Harness, mint) -> None:
    repo, rid = unique_repo()
    token = mint(
        repository=repo,
        repository_id=rid,
        ref="refs/pull/17/merge",
        head_ref="fix-retries",
        base_ref="main",
        event_name="pull_request",
    )
    r = await h.upload(make_results(), bearer=token)
    assert r.status_code == 202
    rep = await _report(h, r.json()["report_id"])
    assert (rep.pr_number, rep.branch, rep.base_branch) == (17, "fix-retries", "main")


@pytest.mark.parametrize("event", ["workflow_run", "pull_request_target"])
async def test_oidc_base_repo_events_rejected(h: Harness, mint, event: str) -> None:
    # These events carry the default branch in `ref` even for fork code, so they
    # must never be filed under main.
    repo, rid = unique_repo()
    token = mint(repository=repo, repository_id=rid, event_name=event)
    r = await h.upload(make_results(), bearer=token)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "rejected"
    assert event in r.json()["error"]["message"]


async def test_token_upload_and_last_used(h: Harness) -> None:
    from polygraph_cloud.ids import new_id

    async with h.ctx.sessionmaker() as db:
        db.add(
            Project(
                id=new_id("prj"),
                owner="octo-demo",
                name="tokens",
                full_name_lower="octo-demo/tokens",
                is_private=True,
                public_results=False,
                default_branch="main",
            )
        )
        await db.commit()
    await h.login()  # dev-login makes the dev user admin of every local project
    t = await h.client.post(
        "/api/projects/octo-demo/tokens/tokens", json={"name": "laptop"}, headers=h.csrf()
    )
    assert t.status_code == 201, t.text
    token = t.json()["token"]
    assert token.startswith("pgp_") and len(token) == 47
    assert t.json()["prefix"] == token[:8]

    r = await h.upload(make_results(branch="dev", sha="e" * 40), token=token, gz=False)
    assert r.status_code == 202, r.text
    rep = await _report(h, r.json()["report_id"])
    assert (rep.status, rep.source, rep.branch, rep.sha) == ("ready", "token", "dev", "e" * 40)

    lst = await h.client.get("/api/projects/octo-demo/tokens/tokens")
    assert lst.json()["items"][0]["last_used_at"] is not None
    assert "token" not in lst.json()["items"][0]

    # A revoked token stops working at once.
    tok_id = t.json()["id"]
    d = await h.client.delete(f"/api/projects/octo-demo/tokens/tokens/{tok_id}", headers=h.csrf())
    assert d.status_code == 204
    r2 = await h.upload(make_results(), token=token)
    assert r2.status_code == 401


# --------------------------------------------------------------------------- #
# Rejections at the door
# --------------------------------------------------------------------------- #


async def test_no_auth(h: Harness) -> None:
    r = await h.upload(make_results())
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_bad_audience(h: Harness, mint) -> None:
    r = await h.upload(make_results(), bearer=mint(aud="https://someone-else.example.test"))
    assert r.status_code == 401
    assert "audience" in r.json()["error"]["message"]


async def test_wrong_issuer(h: Harness, mint) -> None:
    r = await h.upload(make_results(), bearer=mint(iss="https://issuer.example.test"))
    assert r.status_code == 401


async def test_expired_token(h: Harness, mint) -> None:
    now = int(time.time())
    r = await h.upload(make_results(), bearer=mint(iat=now - 3600, nbf=now - 3600, exp=now - 600))
    assert r.status_code == 401
    assert "expired" in r.json()["error"]["message"]


async def test_not_yet_valid_token(h: Harness, mint) -> None:
    now = int(time.time())
    r = await h.upload(make_results(), bearer=mint(nbf=now + 900, iat=now, exp=now + 1200))
    assert r.status_code == 401


async def test_missing_claim(h: Harness, mint) -> None:
    r = await h.upload(make_results(), bearer=mint(drop=("repository_id",)))
    assert r.status_code == 401


async def test_forged_signature(h: Harness, mint) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    r = await h.upload(make_results(), bearer=mint(key=other))
    assert r.status_code == 401


async def test_unknown_kid_refetches_jwks_once(h: Harness, mint, gh, oidc_key) -> None:
    from conftest import JWKS_URL, jwks_for

    # First call loads the normal key set.
    assert (await h.upload(make_results(), bearer=mint())).status_code == 202
    # GitHub rotates: the new kid appears in the JWKS.
    gh.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_for(oidc_key, kid="rotated")))
    h.ctx.oidc.jwks.min_refresh_s = 0
    r = await h.upload(make_results(), bearer=mint(kid="rotated"))
    assert r.status_code == 202, r.text


async def test_hs256_token_refused(h: Harness) -> None:
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60}, "s" * 32, algorithm="HS256"
    )
    r = await h.upload(make_results(), bearer=forged)
    assert r.status_code == 401


async def test_wrong_schema(h: Harness, mint) -> None:
    data = make_results().model_dump(mode="json", by_alias=True)
    data["schema"] = "polygraph-results/v9"
    import json

    r = await h.upload(
        gzip.compress(json.dumps(data).encode()),
        bearer=mint(),
        headers={"Content-Encoding": "gzip"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsupported_schema"


async def test_invalid_file(h: Harness, mint) -> None:
    import json

    data = make_results().model_dump(mode="json", by_alias=True)
    data["runs"][0]["outcome"] = "teleported"
    r = await h.upload(json.dumps(data).encode(), bearer=mint())
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "bad_request"
    assert "teleported" not in err["message"]  # never echo submitted values


async def test_not_json(h: Harness, mint) -> None:
    r = await h.upload(b"{not json", bearer=mint())
    assert r.status_code == 400


async def test_too_large_compressed(make_harness, make_settings, mint) -> None:
    h = await make_harness(make_settings(max_upload_bytes=2048))
    r = await h.upload(b"x" * 4096, bearer=mint())
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


async def test_gzip_bomb(make_harness, make_settings, mint) -> None:
    h = await make_harness(make_settings(max_uncompressed_bytes=1024 * 1024))
    bomb = gzip.compress(b"0" * (20 * 1024 * 1024), compresslevel=9)  # ~20 KB on the wire
    assert len(bomb) < 64 * 1024
    r = await h.upload(bomb, bearer=mint(), headers={"Content-Encoding": "gzip"})
    assert r.status_code == 413


async def test_too_many_runs(make_harness, make_settings, mint) -> None:
    h = await make_harness(make_settings(max_runs_per_file=3))
    r = await h.upload(make_results(), bearer=mint())
    assert r.status_code == 413


async def test_content_without_opt_in_is_rejected(h: Harness, mint) -> None:
    r = await h.upload(make_results(include_text=True), bearer=mint())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "rejected"


async def test_private_repo_needs_app_installed(h: Harness, mint, gh) -> None:
    repo, rid = unique_repo()
    gh.get(f"{GITHUB_API}/repos/{repo}/installation").mock(return_value=httpx.Response(404))
    r = await h.upload(
        make_results(), bearer=mint(repository=repo, repository_id=rid, visibility="private")
    )
    assert r.status_code == 403


async def test_private_repo_with_app_installed(h: Harness, mint, gh) -> None:
    repo, rid = unique_repo()
    route = gh.get(f"{GITHUB_API}/repos/{repo}/installation").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )
    r = await h.upload(
        make_results(), bearer=mint(repository=repo, repository_id=rid, visibility="private")
    )
    assert r.status_code == 202
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer ")
    async with h.ctx.sessionmaker() as db:
        p = (await db.execute(select(Project).where(Project.github_repo_id == rid))).scalar_one()
    assert p.is_private and not p.public_results and p.installation_id == 99


async def test_rate_limit_per_project(make_harness, make_settings, mint) -> None:
    h = await make_harness(make_settings(uploads_per_project_per_hour=2))
    tok = mint()
    assert (await h.upload(make_results(), bearer=tok)).status_code == 202
    assert (await h.upload(make_results(), bearer=tok)).status_code == 202
    r = await h.upload(make_results(), bearer=tok)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) > 0


async def test_rate_limit_per_ip(make_harness, make_settings) -> None:
    h = await make_harness(make_settings(uploads_per_ip_per_hour=2))
    for _ in range(2):
        assert (await h.upload(make_results())).status_code == 401
    assert (await h.upload(make_results())).status_code == 429


# --------------------------------------------------------------------------- #
# Worker-side rejections (text opted in)
# --------------------------------------------------------------------------- #


async def _opt_in_project(h: Harness, mint) -> tuple[str, str]:
    repo, rid = unique_repo()
    token = mint(repository=repo, repository_id=rid)
    assert (await h.upload(make_results(), bearer=token)).status_code == 202
    async with h.ctx.sessionmaker() as db:
        p = (await db.execute(select(Project).where(Project.github_repo_id == rid))).scalar_one()
        p.allow_text_upload = True
        await db.commit()
    return repo, token


async def test_pii_found_rejects_and_deletes_raw(h: Harness, mint) -> None:
    _, token = await _opt_in_project(h, mint)
    results = make_results(
        include_text=True, text="Receipt sent to invented.person@nonexistent-fixture-domain.co.in"
    )
    r = await h.upload(results, bearer=token)
    assert r.status_code == 202
    rep = await _report(h, r.json()["report_id"])
    assert rep.status == "rejected"
    assert "email address" in rep.rejection_reason
    assert "run-control-0" in rep.rejection_reason
    assert "nonexistent-fixture" not in rep.rejection_reason  # never echo the value
    assert rep.raw_key is None
    keys = await h.ctx.storage.list_keys(f"raw/{rep.project_id}/{rep.id}")
    assert keys == []


async def test_reserved_domain_allowed(h: Harness, mint) -> None:
    _, token = await _opt_in_project(h, mint)
    text = "Receipt sent to asha@example.test, copy to ops@mail.example.com from 192.0.2.10"
    r = await h.upload(make_results(include_text=True, text=text), bearer=token)
    rep = await _report(h, r.json()["report_id"])
    assert rep.status == "ready", rep.rejection_reason
    assert rep.content_included is True


async def test_aadhaar_with_valid_checksum_rejected(h: Harness, mint) -> None:
    from polygraph_cloud.pii import verhoeff_check_digit

    number = "23456789012"
    aadhaar = number + verhoeff_check_digit(number)
    _, token = await _opt_in_project(h, mint)
    r = await h.upload(
        make_results(include_text=True, text=f"id {aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}"),
        bearer=token,
    )
    rep = await _report(h, r.json()["report_id"])
    assert rep.status == "rejected" and "Aadhaar" in rep.rejection_reason


async def test_upload_status_needs_matching_credentials(h: Harness, mint) -> None:
    r = await h.upload(make_results(), bearer=mint())
    rid = r.json()["report_id"]
    other_repo, other_id = unique_repo()
    s = await h.client.get(
        f"/api/upload/{rid}",
        headers={"Authorization": f"Bearer {mint(repository=other_repo, repository_id=other_id)}"},
    )
    assert s.status_code == 404


async def test_body_of_helper_roundtrip() -> None:
    from polygraph.results import parse

    res = make_results(runs=[make_run("run-only-0000", "recovered", None)])
    assert parse(gzip.decompress(body_of(res))).runs[0].id == "run-only-0000"
