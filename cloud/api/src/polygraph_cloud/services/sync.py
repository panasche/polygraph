"""Sync a user's GitHub App installations into projects and cached permissions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..context import AppContext
from ..github import GitHubError, RepoAccess, UserTokens
from ..ids import new_id
from ..logs import get_logger
from ..models import GithubCredential, GithubInstallation, Project, ProjectMember, User

log = get_logger(__name__)


async def store_tokens(ctx: AppContext, db: AsyncSession, user_id: str, tokens: UserTokens) -> None:
    enc = ctx.encryptor
    cred = await db.get(GithubCredential, user_id)
    if cred is None:
        cred = GithubCredential(user_id=user_id, access_token_enc="")
        db.add(cred)
    cred.access_token_enc = enc.encrypt(tokens.access_token)
    cred.access_expires_at = tokens.access_expires_at
    cred.refresh_token_enc = enc.encrypt(tokens.refresh_token) if tokens.refresh_token else None
    cred.refresh_expires_at = tokens.refresh_expires_at


async def access_token(ctx: AppContext, db: AsyncSession, user: User) -> str:
    """A usable user-to-server token, refreshing it when it has expired."""
    cred = await db.get(GithubCredential, user.id)
    if cred is None:
        raise GitHubError("No GitHub authorization on file.", 401)
    now = datetime.now(UTC)
    token = ctx.encryptor.decrypt(cred.access_token_enc)
    if token and (
        cred.access_expires_at is None or cred.access_expires_at > now + timedelta(minutes=2)
    ):
        return token
    refresh = ctx.encryptor.decrypt(cred.refresh_token_enc) if cred.refresh_token_enc else None
    if not refresh or (cred.refresh_expires_at is not None and cred.refresh_expires_at <= now):
        raise GitHubError("GitHub authorization expired.", 401)
    tokens = await ctx.github.refresh(refresh)
    await store_tokens(ctx, db, user.id, tokens)
    return tokens.access_token


async def upsert_project(db: AsyncSession, repo: RepoAccess) -> Project | None:
    full_lower = f"{repo.owner}/{repo.name}".lower()
    project = (
        await db.execute(select(Project).where(Project.github_repo_id == repo.repo_id))
    ).scalar_one_or_none()
    by_name = (
        await db.execute(select(Project).where(Project.full_name_lower == full_lower))
    ).scalar_one_or_none()
    if project is None and by_name is not None:
        if by_name.github_repo_id is None:
            project = by_name  # created locally before we knew the repo id
            project.github_repo_id = repo.repo_id
        else:
            log.warning("project_name_conflict", project_id=by_name.id)
            return None
    if project is None:
        project = Project(
            id=new_id("prj"),
            github_repo_id=repo.repo_id,
            owner=repo.owner,
            name=repo.name,
            full_name_lower=full_lower,
            is_private=repo.private,
            public_results=not repo.private,
            allow_text_upload=False,
            default_branch=repo.default_branch,
        )
        db.add(project)
    else:
        if project.full_name_lower != full_lower and (by_name is None or by_name is project):
            project.owner, project.name, project.full_name_lower = repo.owner, repo.name, full_lower
        if repo.private and not project.is_private:
            project.is_private = True
            project.public_results = False  # a repo going private hides its results at once
        elif not repo.private and project.is_private:
            project.is_private = False  # results stay hidden until an admin turns them on
        project.default_branch = repo.default_branch
    project.installation_id = repo.installation_id
    return project


async def sync_user(ctx: AppContext, db: AsyncSession, user: User) -> list[str]:
    """Refresh the user's projects and roles from GitHub. Returns project ids. Caller commits."""
    token = await access_token(ctx, db, user)
    installations = await ctx.github.installations(token)
    now = datetime.now(UTC)
    seen: dict[str, ProjectMember] = {}
    for inst in installations:
        row = await db.get(GithubInstallation, inst.id)
        if row is None:
            db.add(
                GithubInstallation(
                    id=inst.id, account_login=inst.account_login, account_type=inst.account_type
                )
            )
        else:
            row.account_login, row.account_type = inst.account_login, inst.account_type
        await db.flush()
        for repo in await ctx.github.installation_repos(token, inst.id):
            project = await upsert_project(db, repo)
            if project is None:
                continue
            await db.flush()
            member = await db.get(ProjectMember, (project.id, user.id))
            if member is None:
                member = ProjectMember(project_id=project.id, user_id=user.id, role=repo.role)
                db.add(member)
            member.role = repo.role
            member.github_permission = repo.permission
            member.synced_at = now
            seen[project.id] = member
    # Access GitHub no longer reports is access we no longer grant.
    stmt = delete(ProjectMember).where(ProjectMember.user_id == user.id)
    if seen:
        stmt = stmt.where(ProjectMember.project_id.not_in(list(seen)))
    await db.execute(stmt)
    user.last_synced_at = now
    return list(seen)
