"""Operator CLI: ``python -m polygraph_cloud.cli <command>``.

migrate                                   run Alembic migrations to head
seed --results FILE --project owner/repo  load a results file through the real pipeline
     [--branch main] [--sha abc1234] [--public]
create-token --project owner/repo [--name cli]   print a new upload token (shown once)
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import anyio
import anyio.to_thread
from sqlalchemy import select

from . import runtime
from .config import Settings, get_settings
from .context import AppContext, build_context
from .ids import new_id
from .logs import configure_logging
from .models import Project, ProjectMember, User

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def alembic_config(settings: Settings):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.sync_database_url.replace("%", "%%"))
    return cfg


def migrate(settings: Settings, revision: str = "head") -> None:
    from alembic import command

    if settings.is_sqlite:
        path = settings.sync_database_url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(settings), revision)


def _split(full: str) -> tuple[str, str]:
    owner, sep, repo = full.partition("/")
    if not sep or not owner or not repo or "/" in repo:
        raise SystemExit("--project must look like owner/repo")
    return owner, repo


async def _project(ctx: AppContext, full: str, public: bool | None) -> Project:
    owner, repo = _split(full)
    async with ctx.sessionmaker() as db:
        project = (
            await db.execute(select(Project).where(Project.full_name_lower == full.lower()))
        ).scalar_one_or_none()
        if project is None:
            is_public = bool(public)
            project = Project(
                id=new_id("prj"),
                owner=owner,
                name=repo,
                full_name_lower=full.lower(),
                is_private=not is_public,
                public_results=is_public,
                default_branch="main",
            )
            db.add(project)
        elif public is not None:
            project.is_private = not public
            project.public_results = public
        await db.flush()
        if ctx.settings.is_dev:
            # Local demos: every dev-login user administers seeded projects.
            devs = (await db.execute(select(User).where(User.github_id < 0))).scalars().all()
            for u in devs:
                if await db.get(ProjectMember, (project.id, u.id)) is None:
                    db.add(
                        ProjectMember(
                            project_id=project.id,
                            user_id=u.id,
                            role="admin",
                            github_permission="admin",
                            synced_at=datetime.now(UTC),
                        )
                    )
        await db.commit()
        return project


async def seed(
    ctx: AppContext,
    results_path: Path,
    full: str,
    branch: str | None,
    sha: str | None,
    public: bool | None,
) -> str:
    from .services.ingest import check_content_policy, decode_body, parse_results
    from .services.processing import drain_inline

    s = ctx.settings
    raw = results_path.read_bytes()
    decoded = decode_body(raw, None, s.max_uncompressed_bytes)
    results = parse_results(decoded, s.max_runs_per_file)
    if branch or sha:
        git = results.environment.git
        results.environment.git = git.model_copy(
            update={k: v for k, v in {"branch": branch, "sha": sha}.items() if v}
        )
        decoded = results.to_json().encode()
    project = await _project(ctx, full, public)
    check_content_policy(results, project)

    from .services.ingest import create_report

    async with ctx.sessionmaker() as db:
        project = await db.get(Project, project.id)
        assert project is not None
        report = await create_report(ctx, db, project, results, decoded, source="token")
        report_id = report.id
        await db.commit()
    await ctx.queue.send({"report_id": report_id})
    if ctx.queue.inline:
        await drain_inline(ctx)
    return report_id


async def create_token(ctx: AppContext, full: str, name: str) -> str:
    from .api.projects import issue_token

    owner, repo = _split(full)
    async with ctx.sessionmaker() as db:
        project = (
            await db.execute(select(Project).where(Project.full_name_lower == full.lower()))
        ).scalar_one_or_none()
        if project is None:
            raise SystemExit(f"No project {owner}/{repo}. Seed or sync it first.")
        token, row = issue_token(project, name, None)
        row.created_by_login = "cli"
        db.add(row)
        await db.commit()
    return token


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.check()
    ctx = build_context(settings)
    try:
        if settings.is_dev and settings.is_sqlite:
            await anyio.to_thread.run_sync(migrate, settings)
        if args.command == "seed":
            path = Path(args.results)
            if not path.is_file():
                raise SystemExit(f"No such file: {path}")
            report_id = await seed(ctx, path, args.project, args.branch, args.sha, args.public)
            async with ctx.sessionmaker() as db:
                from .models import Report

                r = await db.get(Report, report_id)
                status = r.status if r else "missing"
                reason = r.rejection_reason if r else None
            print(f"report {report_id}: {status}" + (f" ({reason})" if reason else ""))
            print(f"{settings.public_base_url}/app/{args.project}/reports/{report_id}")
            return 0 if status in ("ready", "queued") else 1
        if args.command == "create-token":
            token = await create_token(ctx, args.project, args.name)
            print(token)
            print("Store it now (e.g. as POLYGRAPH_TOKEN). It is not shown again.", file=sys.stderr)
            return 0
    finally:
        await ctx.aclose()
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m polygraph_cloud.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("migrate", help="run database migrations")
    m.add_argument("--revision", default="head")
    sd = sub.add_parser("seed", help="load a results file through the real pipeline")
    sd.add_argument("--results", required=True)
    sd.add_argument("--project", required=True)
    sd.add_argument("--branch")
    sd.add_argument("--sha")
    sd.add_argument("--public", action="store_true", default=None)
    ct = sub.add_parser("create-token", help="create a per-project upload token")
    ct.add_argument("--project", required=True)
    ct.add_argument("--name", default="cli")
    args = parser.parse_args(argv)
    if args.command == "migrate":
        settings = get_settings()
        configure_logging(settings.log_level)
        migrate(settings, args.revision)
        print("migrations applied")
        return 0
    return runtime.run(_run, args)


if __name__ == "__main__":
    sys.exit(main())
