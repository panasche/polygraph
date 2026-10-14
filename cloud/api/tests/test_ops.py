"""Retention job, migrations and the CLI."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, update

from conftest import TEST_DB, Harness, make_results, unique_repo
from polygraph_cloud.jobs.prune import prune
from polygraph_cloud.models import Base, Report, Session


async def test_prune_retention(h: Harness, mint) -> None:
    repo, rid = unique_repo()
    tok = mint(repository=repo, repository_id=rid)
    old = (await h.upload(make_results(), bearer=tok)).json()["report_id"]
    ancient = (await h.upload(make_results(), bearer=tok)).json()["report_id"]
    fresh = (await h.upload(make_results(), bearer=tok)).json()["report_id"]
    now = datetime.now(UTC)
    async with h.ctx.sessionmaker() as db:
        await db.execute(
            update(Report).where(Report.id == old).values(created_at=now - timedelta(days=91))
        )
        await db.execute(
            update(Report).where(Report.id == ancient).values(created_at=now - timedelta(days=731))
        )
        pid = (await db.get(Report, fresh)).project_id
        await db.commit()

    dry = await prune(h.ctx, dry_run=True)
    assert dry.reports_deleted == 1
    assert await h.ctx.storage.list_keys(f"runs/{pid}/{old}/")

    stats = await prune(h.ctx)
    assert stats.reports_deleted == 1 and stats.run_files_reports == 1
    async with h.ctx.sessionmaker() as db:
        o = await db.get(Report, old)
        assert o is not None and o.runs_purged_at is not None and o.raw_key is None
        assert await db.get(Report, ancient) is None
        assert (await db.get(Report, fresh)).runs_purged_at is None
    assert await h.ctx.storage.list_keys(f"runs/{pid}/{old}/") == []
    assert await h.ctx.storage.list_keys(f"runs/{pid}/{ancient}/") == []
    assert await h.ctx.storage.list_keys(f"runs/{pid}/{fresh}/")

    # Summary numbers survive, the run page explains the files are gone.
    async with h.anon() as anon:
        assert (await anon.get(f"/api/projects/{repo}/reports/{old}")).status_code == 200
        r = await anon.get(f"/api/projects/{repo}/reports/{old}/runs/run-lost-0002")
        assert r.status_code == 404 and "90 days" in r.json()["error"]["message"]


async def test_prune_expired_sessions(h: Harness) -> None:
    await h.login()
    async with h.ctx.sessionmaker() as db:
        await db.execute(update(Session).values(expires_at=datetime.now(UTC) - timedelta(days=1)))
        await db.commit()
    stats = await prune(h.ctx)
    assert stats.sessions_deleted == 1
    assert (await h.client.get("/api/me")).status_code == 401


@pytest.mark.skipif(bool(TEST_DB), reason="SQLite migration check")
def test_alembic_migrations_match_models(tmp_path: Path) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from polygraph_cloud.cli import migrate
    from polygraph_cloud.config import Settings

    db = tmp_path / "mig.db"
    settings = Settings(database_url=f"sqlite:///{db.as_posix()}")
    migrate(settings)
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
    migrate(settings)  # idempotent


async def test_cli_seed_and_token(make_settings, tmp_path: Path, gh) -> None:
    from polygraph_cloud.cli import create_token, seed
    from polygraph_cloud.context import build_context
    from polygraph_cloud.db import create_all

    settings = make_settings()
    ctx = build_context(settings)
    try:
        if TEST_DB:
            async with ctx.engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        await create_all(ctx.engine)
        f = tmp_path / "results.json.gz"
        f.write_bytes(gzip.compress(make_results().to_json().encode()))
        rid = await seed(ctx, f, "octo-demo/seeded", "release", "f" * 40, True)
        async with ctx.sessionmaker() as db:
            rep = (await db.execute(select(Report).where(Report.id == rid))).scalar_one()
        assert rep.status == "ready" and rep.branch == "release" and rep.sha == "f" * 40
        token = await create_token(ctx, "octo-demo/seeded", "ci")
        assert token.startswith("pgp_")
    finally:
        await ctx.aclose()
