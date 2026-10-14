"""Export and real deletion (DB rows and storage objects)."""

from __future__ import annotations

from sqlalchemy import func, select

from conftest import Harness, make_results, unique_repo
from polygraph_cloud.models import (
    AuditLog,
    Project,
    ProjectMember,
    Report,
    ReportPair,
    RunIndex,
    Session,
    UploadToken,
    User,
)


async def _count(h: Harness, model, *where) -> int:
    async with h.ctx.sessionmaker() as db:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return (await db.execute(q)).scalar_one()


async def _seeded(h: Harness, mint) -> tuple[str, str, str]:
    repo, rid = unique_repo()
    r = await h.upload(make_results(), bearer=mint(repository=repo, repository_id=rid))
    report_id = r.json()["report_id"]
    async with h.ctx.sessionmaker() as db:
        pid = (
            await db.execute(select(Project.id).where(Project.github_repo_id == rid))
        ).scalar_one()
    return repo, report_id, pid


async def test_delete_report_removes_rows_and_files(h: Harness, mint) -> None:
    repo, report_id, pid = await _seeded(h, mint)
    await h.login()
    assert await h.ctx.storage.list_keys(f"runs/{pid}/{report_id}/")
    r = await h.client.delete(f"/api/projects/{repo}/reports/{report_id}", headers=h.csrf())
    assert r.status_code == 204
    assert await h.ctx.storage.list_keys(f"runs/{pid}/{report_id}/") == []
    assert await h.ctx.storage.list_keys(f"raw/{pid}/") == []
    assert await _count(h, Report, Report.id == report_id) == 0
    assert await _count(h, ReportPair, ReportPair.report_id == report_id) == 0
    assert await _count(h, RunIndex, RunIndex.report_id == report_id) == 0
    assert (await h.client.get(f"/api/projects/{repo}/reports/{report_id}")).status_code == 404


async def test_delete_project_removes_everything(h: Harness, mint) -> None:
    repo, _, pid = await _seeded(h, mint)
    await h.login()
    t = await h.client.post(f"/api/projects/{repo}/tokens", json={"name": "ci"}, headers=h.csrf())
    assert t.status_code == 201
    r = await h.client.delete(f"/api/projects/{repo}", headers=h.csrf())
    assert r.status_code == 204
    assert await h.ctx.storage.list_keys(f"raw/{pid}/") == []
    assert await h.ctx.storage.list_keys(f"runs/{pid}/") == []
    for model, col in (
        (Report, Report.project_id),
        (UploadToken, UploadToken.project_id),
        (ProjectMember, ProjectMember.project_id),
    ):
        assert await _count(h, model, col == pid) == 0
    assert await _count(h, Project, Project.id == pid) == 0
    assert await _count(h, AuditLog, AuditLog.action == "project.delete") == 1


async def test_export_contains_account_data_and_no_secrets(h: Harness, mint) -> None:
    repo, _, _ = await _seeded(h, mint)
    user = await h.login("export-user")
    await h.client.post(f"/api/projects/{repo}/tokens", json={"name": "laptop"}, headers=h.csrf())
    r = await h.client.get("/api/me/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["user"]["id"] == user["id"] and data["user"]["login"] == "export-user"
    assert data["user"]["consented_privacy_version"] == "2026-10-13"
    assert data["project_memberships"][0]["project"].lower() == repo.lower()
    assert data["upload_tokens_created"][0]["name"] == "laptop"
    assert {e["action"] for e in data["activity"]} >= {
        "auth.dev_login",
        "account.consent",
        "token.create",
    }
    assert len(data["sessions"]) == 1
    text = r.text
    assert "pgp_" not in text.replace(data["upload_tokens_created"][0]["prefix"], "")
    assert "token_hash" not in text and h.client.cookies.get("pg_session") not in text


async def test_delete_account_sole_admin_deletes_project(h: Harness, mint) -> None:
    repo, _, pid = await _seeded(h, mint)
    user = await h.login("leaving-user")
    await h.client.post(f"/api/projects/{repo}/tokens", json={"name": "ci"}, headers=h.csrf())
    r = await h.client.delete("/api/me", headers=h.csrf())
    assert r.status_code == 204
    assert await _count(h, User, User.id == user["id"]) == 0
    assert await _count(h, Session) == 0
    assert await _count(h, Project, Project.id == pid) == 0  # they were the only admin
    assert await h.ctx.storage.list_keys(f"runs/{pid}/") == []
    assert await _count(h, AuditLog, AuditLog.actor_user_id == user["id"]) == 0
    h.client.cookies.clear()
    assert (await h.client.get("/api/me")).status_code == 401


async def test_delete_account_keeps_shared_projects(h: Harness, mint) -> None:
    repo, _, pid = await _seeded(h, mint)
    await h.login("other-admin")
    async with h.anon() as second:
        r = await second.post("/api/auth/dev-login", json={"login": "leaving-user"})
        csrf = second.cookies.get("pg_csrf")
        await second.post(
            "/api/me/consent",
            json={"privacy_version": "2026-10-13"},
            headers={"X-CSRF-Token": csrf},
        )
        t = await second.post(
            f"/api/projects/{repo}/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": csrf}
        )
        assert t.status_code == 201
        d = await second.delete("/api/me", headers={"X-CSRF-Token": csrf})
        assert d.status_code == 204
    assert r.status_code == 200
    assert await _count(h, Project, Project.id == pid) == 1
    async with h.ctx.sessionmaker() as db:
        tok = (await db.execute(select(UploadToken))).scalar_one()
    assert tok.created_by_login == "[deleted]" and tok.created_by_user_id is None
