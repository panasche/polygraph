"""Visibility rules and the read APIs: projects, reports, runs, trends, compare, badge, leaderboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from conftest import GITHUB_API, Harness, make_results, make_run, unique_repo
from polygraph_cloud.badge import AMBER, CRIMSON, GREEN, GREY
from polygraph_cloud.ids import new_id
from polygraph_cloud.models import Project, ProjectMember, Report, User


async def _private_project(h: Harness, mint, gh) -> tuple[str, str, str]:
    repo, rid = unique_repo()
    gh.get(f"{GITHUB_API}/repos/{repo}/installation").mock(
        return_value=httpx.Response(200, json={"id": 31})
    )
    token = mint(repository=repo, repository_id=rid, visibility="private")
    r = await h.upload(make_results(), bearer=token)
    assert r.status_code == 202
    return repo, r.json()["report_id"], token


async def _public_project(h: Harness, mint, **kw) -> tuple[str, str, str]:
    repo, rid = unique_repo()
    token = mint(repository=repo, repository_id=rid, **kw)
    r = await h.upload(make_results(), bearer=token)
    assert r.status_code == 202
    return repo, r.json()["report_id"], token


async def _add_member(h: Harness, repo: str, login: str, role: str) -> None:
    async with h.ctx.sessionmaker() as db:
        p = (
            await db.execute(select(Project).where(Project.full_name_lower == repo.lower()))
        ).scalar_one()
        u = (await db.execute(select(User).where(User.login == login))).scalar_one()
        m = await db.get(ProjectMember, (p.id, u.id))
        if m is None:
            db.add(ProjectMember(project_id=p.id, user_id=u.id, role=role))
        else:
            m.role = role
        await db.commit()


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #


async def test_private_project_is_404_for_outsiders(h: Harness, mint, gh) -> None:
    repo, rid, _ = await _private_project(h, mint, gh)
    async with h.anon() as anon:
        for path in (
            f"/api/projects/{repo}",
            f"/api/projects/{repo}/reports",
            f"/api/projects/{repo}/reports/{rid}",
            f"/api/projects/{repo}/reports/{rid}/runs/run-lost-0002",
            f"/api/projects/{repo}/trends",
            f"/api/projects/{repo}/branches",
            f"/api/projects/{repo}/compare?base=branch:main&head={rid}",
            f"/api/upload/{rid}",
        ):
            r = await anon.get(path)
            assert r.status_code == 404, path
        unknown = await anon.get("/api/projects/nobody-here/nothing")
        priv = await anon.get(f"/api/projects/{repo}")
        assert (
            unknown.json()
            == priv.json()
            == {"error": {"code": "not_found", "message": "Project not found."}}
        )


async def test_signed_in_non_member_also_gets_404(h: Harness, mint, gh) -> None:
    repo, _, _ = await _private_project(h, mint, gh)
    await h.login("stranger")  # dev-login makes them admin of *existing* projects...
    async with h.ctx.sessionmaker() as db:  # ...so strip that to simulate an outsider
        await db.execute(ProjectMember.__table__.delete())
        await db.commit()
    assert (await h.client.get(f"/api/projects/{repo}")).status_code == 404


async def test_badge_is_neutral_for_private_and_unknown(h: Harness, mint, gh) -> None:
    repo, _, _ = await _private_project(h, mint, gh)
    async with h.anon() as anon:
        priv = await anon.get(f"/api/badge/{repo}.svg")
        unknown = await anon.get("/api/badge/nobody-here/nothing.svg")
    assert priv.status_code == unknown.status_code == 200
    assert priv.content == unknown.content
    assert b"unknown" in priv.content and GREY.encode() in priv.content
    assert priv.headers["cache-control"] == "public, max-age=300"
    assert priv.headers["content-type"].startswith("image/svg+xml")


async def test_public_badge_shows_numbers(h: Harness, mint) -> None:
    repo, _, _ = await _public_project(h, mint)
    async with h.anon() as anon:
        r = await anon.get(f"/api/badge/{repo}.svg?branch=main")
    svg = r.text
    assert "silent-lie 17% · coverage 2/3" in svg  # 1 of 6 faulted runs
    assert CRIMSON in svg
    assert r.headers["cache-control"] == "public, max-age=300"
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"


async def test_public_results_switch_off_hides_everything(h: Harness, mint) -> None:
    repo, rid, _ = await _public_project(h, mint)
    await h.login()
    p = await h.client.patch(
        f"/api/projects/{repo}", json={"public_results": False}, headers=h.csrf()
    )
    assert p.status_code == 200 and p.json()["public_results"] is False
    async with h.anon() as anon:
        assert (await anon.get(f"/api/projects/{repo}/reports/{rid}")).status_code == 404
        assert b"unknown" in (await anon.get(f"/api/badge/{repo}.svg")).content
    # Members still see it.
    assert (await h.client.get(f"/api/projects/{repo}/reports/{rid}")).status_code == 200


async def test_private_repo_results_cannot_be_made_public(h: Harness, mint, gh) -> None:
    repo, _, _ = await _private_project(h, mint, gh)
    await h.login()
    r = await h.client.patch(
        f"/api/projects/{repo}", json={"public_results": True}, headers=h.csrf()
    )
    assert r.status_code == 400


async def test_repo_turning_private_hides_results(h: Harness, mint) -> None:
    repo, rid_num = unique_repo()
    await h.upload(make_results(), bearer=mint(repository=repo, repository_id=rid_num))
    await h.upload(
        make_results(), bearer=mint(repository=repo, repository_id=rid_num, visibility="private")
    )
    async with h.anon() as anon:
        assert (await anon.get(f"/api/projects/{repo}")).status_code == 404


async def test_roles(h: Harness, mint) -> None:
    repo, rid, _ = await _public_project(h, mint)
    async with h.anon() as anon:
        assert (await anon.get(f"/api/projects/{repo}")).json()["role"] == "viewer"
    await h.login("member-user")
    await _add_member(h, repo, "member-user", "member")
    me = await h.client.get(f"/api/projects/{repo}")
    assert me.json()["role"] == "member"
    # Members can read, but admin actions are 403 (they already know it exists).
    assert (await h.client.get(f"/api/projects/{repo}/tokens")).status_code == 403
    r = await h.client.delete(f"/api/projects/{repo}/reports/{rid}", headers=h.csrf())
    assert r.status_code == 403
    r = await h.client.patch(
        f"/api/projects/{repo}", json={"allow_text_upload": True}, headers=h.csrf()
    )
    assert r.status_code == 403


async def test_stale_membership_ignored(h: Harness, mint, gh) -> None:
    repo, _, _ = await _private_project(h, mint, gh)
    async with h.ctx.sessionmaker() as db:
        u = User(id=new_id("usr"), github_id=777, login="invented-user")
        db.add(u)
        await db.flush()
        p = (
            await db.execute(select(Project).where(Project.full_name_lower == repo.lower()))
        ).scalar_one()
        db.add(
            ProjectMember(
                project_id=p.id,
                user_id=u.id,
                role="member",
                synced_at=datetime.now(UTC) - timedelta(days=3),
            )
        )
        await db.commit()
        from polygraph_cloud.services.access import get_member

        assert await get_member(db, p, u, h.ctx.settings) is None


# --------------------------------------------------------------------------- #
# Read APIs
# --------------------------------------------------------------------------- #


async def test_project_and_report_shapes(h: Harness, mint) -> None:
    repo, rid, _ = await _public_project(h, mint)
    async with h.anon() as anon:
        p = (await anon.get(f"/api/projects/{repo}")).json()
        assert set(p) == {
            "id",
            "owner",
            "repo",
            "full_name",
            "is_private",
            "public_results",
            "allow_text_upload",
            "default_branch",
            "created_at",
            "last_report_at",
            "role",
            "latest",
        }
        assert p["latest"]["id"] == rid
        lite = p["latest"]
        assert set(lite) == {
            "id",
            "project",
            "status",
            "rejection_reason",
            "source",
            "branch",
            "sha",
            "pr_number",
            "created_at",
            "framework",
            "model",
            "config_label",
            "runs",
            "faulted_runs",
            "success",
            "silent_lie",
            "double_action",
            "hijack",
            "coverage",
        }
        assert lite["silent_lie"]["numerator"] == 1 and lite["silent_lie"]["denominator"] == 6
        assert (
            0
            < lite["silent_lie"]["ci_low"]
            < lite["silent_lie"]["value"]
            < lite["silent_lie"]["ci_high"]
        )
        assert lite["coverage"] == {"applicable": 3, "covered": 2}
        assert lite["created_at"].endswith("Z")

        d = (await anon.get(f"/api/projects/{repo}/reports/{rid}")).json()
        assert isinstance(d["runs"], int) and isinstance(d["run_list"], list)
        assert [r["outcome"] for r in d["run_list"][:2]] == ["double_action", "silent_lie"]
        cell = next(c for c in d["heatmap"] if c["fault"] == "lost_response")
        assert cell == {
            "server": "bank",
            "tool": "pay_invoice",
            "fault": "lost_response",
            "runs": 3,
            "outcomes": {"recovered": 2, "double_action": 1},
            "bad": 1,
        }
        assert d["heatmap"][0]["bad"] >= d["heatmap"][-1]["bad"]
        assert d["environment"]["framework"] == "langgraph"
        assert d["summary"]["silent_lie"]["numerator"] == 1
        assert d["coverage_detail"]["pairs"][2]["runs_done"] == 1
        assert d["tools"][0]["name"] == "pay_invoice"
        assert d["content_included"] is False

        run = (await anon.get(f"/api/projects/{repo}/reports/{rid}/runs/run-lost-0002")).json()
        assert run["id"] == "run-lost-0002" and run["outcome"] == "double_action"
        assert run["report_id"] == rid and run["content_included"] is False
        assert run["ledger"][0]["told"]["text"] is None
        assert (
            await anon.get(f"/api/projects/{repo}/reports/{rid}/runs/nope-nope-nope")
        ).status_code == 404
        assert (
            await anon.get(f"/api/projects/{repo}/reports/{rid}/runs/..%2F..%2Fsecret")
        ).status_code == 404


async def test_reports_pagination_and_filters(h: Harness, mint) -> None:
    repo, rid_num = unique_repo()
    ids = []
    for i in range(5):
        ref = "refs/heads/main" if i % 2 == 0 else "refs/pull/3/merge"
        r = await h.upload(
            make_results(),
            bearer=mint(repository=repo, repository_id=rid_num, ref=ref, head_ref="feat"),
        )
        ids.append(r.json()["report_id"])
    async with h.anon() as anon:
        page1 = (await anon.get(f"/api/projects/{repo}/reports?limit=2")).json()
        assert [r["id"] for r in page1["items"]] == ids[::-1][:2]
        page2 = (
            await anon.get(
                f"/api/projects/{repo}/reports", params={"limit": 2, "cursor": page1["next_cursor"]}
            )
        ).json()
        assert [r["id"] for r in page2["items"]] == ids[::-1][2:4]
        page3 = (
            await anon.get(
                f"/api/projects/{repo}/reports", params={"limit": 2, "cursor": page2["next_cursor"]}
            )
        ).json()
        assert page3["next_cursor"] is None and len(page3["items"]) == 1
        prs = (await anon.get(f"/api/projects/{repo}/reports?pr=3")).json()["items"]
        assert len(prs) == 2 and all(r["pr_number"] == 3 for r in prs)
        main = (await anon.get(f"/api/projects/{repo}/reports?branch=main")).json()["items"]
        assert len(main) == 3
        bad = await anon.get(f"/api/projects/{repo}/reports?cursor=%%%")
        assert bad.status_code == 400
        br = (await anon.get(f"/api/projects/{repo}/branches")).json()["items"]
        assert {b["name"]: b["reports"] for b in br} == {"main": 3, "feat": 2}
        assert all(b["last_report_at"].endswith("Z") for b in br)


async def test_trends(h: Harness, mint) -> None:
    repo, rid_num = unique_repo()
    ids = []
    for _ in range(3):
        r = await h.upload(make_results(), bearer=mint(repository=repo, repository_id=rid_num))
        ids.append(r.json()["report_id"])
    async with h.ctx.sessionmaker() as db:  # push the oldest one out of the window
        await db.execute(
            update(Report)
            .where(Report.id == ids[0])
            .values(created_at=datetime.now(UTC) - timedelta(days=40))
        )
        await db.commit()
    async with h.anon() as anon:
        t = (await anon.get(f"/api/projects/{repo}/trends?branch=main&days=30")).json()
        assert t["branch"] == "main"
        assert [p["report_id"] for p in t["points"]] == ids[1:]
        assert set(t["points"][0]) == {
            "report_id",
            "created_at",
            "sha",
            "success",
            "silent_lie",
            "double_action",
            "hijack",
            "coverage",
        }
        t90 = (await anon.get(f"/api/projects/{repo}/trends")).json()
        assert [p["report_id"] for p in t90["points"]] == ids


async def test_compare_by_branch_and_by_id(h: Harness, mint) -> None:
    repo, rid_num = unique_repo()
    base = await h.upload(make_results(), bearer=mint(repository=repo, repository_id=rid_num))
    base_id = base.json()["report_id"]
    better = make_results(
        runs=[
            make_run("run-lost-0000", "recovered", ("bank", "pay_invoice", "lost_response")),
            make_run(
                "run-lost-0001", "recovered", ("bank", "pay_invoice", "lost_response"), repeat=1
            ),
            make_run("run-new-00000", "recovered", ("shop", "refund", "injection")),
        ]
    )
    pr_tok = mint(
        repository=repo,
        repository_id=rid_num,
        ref="refs/pull/9/merge",
        head_ref="fix",
        base_ref="main",
    )
    head = await h.upload(better, bearer=pr_tok)
    head_id = head.json()["report_id"]

    via_upload = await h.client.get(
        f"/api/upload/{head_id}/compare?base=branch:main",
        headers={"Authorization": f"Bearer {pr_tok}"},
    )
    assert via_upload.status_code == 200, via_upload.text
    c = via_upload.json()
    assert c["base"]["id"] == base_id and c["head"]["id"] == head_id
    assert set(c["metrics"]) == {"silent_lie", "double_action", "hijack", "success"}
    assert c["metrics"]["double_action"]["base"]["numerator"] == 1
    assert c["metrics"]["double_action"]["head"]["numerator"] == 0
    assert c["coverage"]["base"] == {"applicable": 3, "covered": 2}
    pairs = {(p["server"], p["tool"], p["fault"]): p for p in c["pairs"]}
    assert pairs[("bank", "pay_invoice", "lost_response")]["base"] == {"runs": 3, "bad": 1}
    assert pairs[("bank", "pay_invoice", "lost_response")]["head"] == {"runs": 2, "bad": 0}
    assert pairs[("bank", "get_balance", "timeout")]["head"] is None

    async with h.anon() as anon:
        by_id = await anon.get(
            f"/api/projects/{repo}/compare", params={"base": base_id, "head": head_id}
        )
        assert by_id.status_code == 200 and by_id.json()["base"]["id"] == base_id
        missing = await anon.get(
            f"/api/projects/{repo}/compare", params={"base": "branch:nope", "head": head_id}
        )
        assert missing.status_code == 404


async def test_compare_conflict_while_processing(h: Harness, mint) -> None:
    repo, rid_num = unique_repo()
    tok = mint(repository=repo, repository_id=rid_num)
    await h.upload(make_results(), bearer=tok)
    r = await h.upload(make_results(), bearer=tok)
    rid = r.json()["report_id"]
    async with h.ctx.sessionmaker() as db:
        await db.execute(update(Report).where(Report.id == rid).values(status="processing"))
        await db.commit()
    c = await h.client.get(f"/api/upload/{rid}/compare", headers={"Authorization": f"Bearer {tok}"})
    assert c.status_code == 409 and c.json()["error"]["code"] == "conflict"


async def test_badge_colours(h: Harness, mint) -> None:
    async def badge_for(runs) -> str:
        repo, rid_num = unique_repo()
        await h.upload(make_results(runs=runs), bearer=mint(repository=repo, repository_id=rid_num))
        async with h.anon() as anon:
            return (await anon.get(f"/api/badge/{repo}.svg")).text

    clean = [
        make_run(f"run-ok-{i:05d}", "recovered", ("bank", "pay_invoice", "lost_response"), repeat=i)
        for i in range(4)
    ]
    assert GREEN in await badge_for(clean)
    low = [
        make_run(f"run-ok-{i:05d}", "recovered", ("bank", "get_balance", "timeout"), repeat=i)
        for i in range(40)
    ] + [make_run("run-lie-00000", "silent_lie", ("bank", "get_balance", "timeout"), repeat=40)]
    svg = await badge_for(low)
    assert AMBER in svg and "silent-lie 2.4%" in svg
    assert GREY in await badge_for([make_run("run-ctrl-0000", "recovered", None)])


async def test_leaderboard(make_harness, make_settings, mint) -> None:
    h = await make_harness(make_settings(leaderboard_project="polygraph-dev/benchmark"))
    async with h.anon() as anon:
        empty = (await anon.get("/api/leaderboard")).json()
    assert empty == {"project": "polygraph-dev/benchmark", "updated_at": None, "entries": []}
    tok = mint(repository="polygraph-dev/benchmark", repository_id=1)
    await h.upload(make_results(framework="langgraph", model="m1"), bearer=tok)
    newest = await h.upload(make_results(framework="langgraph", model="m1"), bearer=tok)
    await h.upload(make_results(framework="crewai", model="m1"), bearer=tok)
    async with h.anon() as anon:
        r = await anon.get("/api/leaderboard")
    lb = r.json()
    assert r.headers["cache-control"] == "public, max-age=300"
    assert len(lb["entries"]) == 2 and lb["updated_at"]
    lg = next(e for e in lb["entries"] if e["framework"] == "langgraph")
    assert lg["report_id"] == newest.json()["report_id"]
    assert lg["framework_version"] == "1.2.0" and lg["runs"] == 7
    assert lg["pass_k"]["k"] >= 1


async def test_ready(h: Harness) -> None:
    r = await h.client.get("/api/ready")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "checks": {"database": "ok", "storage": "ok", "queue": "ok"},
    }
