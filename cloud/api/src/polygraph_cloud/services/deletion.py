"""Real deletion: storage objects first, then database rows (CONTRACTS.md 7)."""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..context import AppContext
from ..logs import get_logger
from ..models import (
    AuditLog,
    GithubCredential,
    Project,
    ProjectMember,
    Report,
    Session,
    UploadToken,
    User,
)
from ..storage import project_prefixes, runs_prefix

log = get_logger(__name__)

DELETED_LOGIN = "[deleted]"


async def delete_report_objects(ctx: AppContext, report: Report) -> None:
    if report.raw_key:
        await ctx.storage.delete(report.raw_key)
    await ctx.storage.delete_prefix(runs_prefix(report.project_id, report.id))


async def delete_report(ctx: AppContext, db: AsyncSession, report: Report) -> None:
    await delete_report_objects(ctx, report)
    await db.delete(report)  # pairs and run index cascade


async def delete_project(ctx: AppContext, db: AsyncSession, project: Project) -> None:
    for prefix in project_prefixes(project.id):
        await ctx.storage.delete_prefix(prefix)
    await db.execute(delete(Report).where(Report.project_id == project.id))
    await db.execute(delete(UploadToken).where(UploadToken.project_id == project.id))
    await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
    await db.delete(project)


async def sole_admin_projects(db: AsyncSession, user_id: str) -> list[Project]:
    """Projects where this user is the only admin we know of."""
    admin_of = (
        select(ProjectMember.project_id)
        .where(ProjectMember.user_id == user_id, ProjectMember.role == "admin")
        .scalar_subquery()
    )
    counts = (
        select(ProjectMember.project_id, func.count().label("n"))
        .where(ProjectMember.role == "admin", ProjectMember.project_id.in_(admin_of))
        .group_by(ProjectMember.project_id)
        .subquery()
    )
    rows = await db.execute(
        select(Project).join(counts, counts.c.project_id == Project.id).where(counts.c.n == 1)
    )
    return list(rows.scalars())


async def delete_user(ctx: AppContext, db: AsyncSession, user: User) -> list[str]:
    """Delete the account. Returns the full names of projects deleted with it."""
    deleted = []
    for project in await sole_admin_projects(db, user.id):
        deleted.append(project.full_name)
        await delete_project(ctx, db, project)

    cred = await db.get(GithubCredential, user.id)
    if cred is not None:
        token = ctx.encryptor.decrypt(cred.access_token_enc)
        if token:
            try:
                await ctx.github.revoke_grant(token)
            except Exception as exc:
                log.warning("github_revoke_failed", error_type=type(exc).__name__)

    await db.execute(
        update(UploadToken)
        .where(UploadToken.created_by_user_id == user.id)
        .values(created_by_user_id=None, created_by_login=DELETED_LOGIN)
    )
    await db.execute(
        update(AuditLog).where(AuditLog.actor_user_id == user.id).values(actor_user_id=None)
    )
    await db.execute(delete(Session).where(Session.user_id == user.id))
    await db.execute(delete(ProjectMember).where(ProjectMember.user_id == user.id))
    await db.execute(delete(GithubCredential).where(GithubCredential.user_id == user.id))
    await db.delete(user)
    return deleted
