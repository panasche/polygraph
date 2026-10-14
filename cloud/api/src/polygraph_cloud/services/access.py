"""Who can see what. Visibility follows GitHub.

- Public repo: results are public by default (``public_results``); an admin can
  switch that off, and then only members see them.
- Private repo: only members (people GitHub says can see the repo).
- Anything a caller can't see answers exactly like something that doesn't exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import forbidden, project_not_found
from ..models import Project, ProjectMember, User


async def find_project(db: AsyncSession, owner: str, repo: str) -> Project | None:
    full = f"{owner}/{repo}".lower()
    if len(full) > 201:
        return None
    return (
        await db.execute(select(Project).where(Project.full_name_lower == full))
    ).scalar_one_or_none()


def membership_is_fresh(member: ProjectMember, user: User, settings: Settings) -> bool:
    if user.github_id < 0:  # development fake users (dev-login only)
        return True
    age = datetime.now(UTC) - member.synced_at
    return age <= timedelta(hours=settings.membership_max_age_hours)


async def get_member(
    db: AsyncSession, project: Project, user: User | None, settings: Settings
) -> ProjectMember | None:
    if user is None:
        return None
    member = await db.get(ProjectMember, (project.id, user.id))
    if member is None or not membership_is_fresh(member, user, settings):
        return None
    return member


def publicly_visible(project: Project) -> bool:
    return not project.is_private and project.public_results


def role_for(project: Project, member: ProjectMember | None) -> str | None:
    if member is not None:
        return member.role
    if publicly_visible(project):
        return "viewer"
    return None


async def require_read(
    db: AsyncSession, owner: str, repo: str, user: User | None, settings: Settings
) -> tuple[Project, str]:
    project = await find_project(db, owner, repo)
    if project is None:
        raise project_not_found()
    member = await get_member(db, project, user, settings)
    role = role_for(project, member)
    if role is None:
        raise project_not_found()
    return project, role


async def require_admin(
    db: AsyncSession, owner: str, repo: str, user: User | None, settings: Settings
) -> Project:
    project, role = await require_read(db, owner, repo, user, settings)
    if role != "admin":
        raise forbidden("Only repository admins can do that.")
    return project
