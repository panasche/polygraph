"""The signed-in account: profile, consent, export, deletion (DPDP Act 2023 rights)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..context import AppContext
from ..errors import bad_request
from ..models import AuditLog, GithubCredential, Project, ProjectMember, Session, UploadToken, User
from ..schemas import ConsentIn, UserOut
from ..services import audit
from ..services.deletion import delete_user
from ..services.serialize import user_out
from .deps import clear_session_cookies, get_ctx, get_db, require_user

router = APIRouter(prefix="/api/me", tags=["account"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


@router.get("", response_model=UserOut)
async def me(user: User = Depends(require_user)) -> UserOut:
    return user_out(user)


@router.post("/consent", response_model=UserOut)
async def consent(
    body: ConsentIn,
    user: User = Depends(require_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    if body.privacy_version != ctx.settings.privacy_version:
        raise bad_request(
            "Unknown privacy notice version. The current version is "
            f"{ctx.settings.privacy_version}."
        )
    user.consented_privacy_version = body.privacy_version
    user.consented_at = datetime.now(UTC)
    audit.record(
        db, "account.consent", actor_user_id=user.id, detail={"version": body.privacy_version}
    )
    await db.commit()
    return user_out(user)


@router.get("/export")
async def export(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Everything tied to this account, as one JSON file."""
    sessions = (await db.execute(select(Session).where(Session.user_id == user.id))).scalars()
    memberships = (
        await db.execute(
            select(ProjectMember, Project)
            .join(Project, Project.id == ProjectMember.project_id)
            .where(ProjectMember.user_id == user.id)
        )
    ).all()
    tokens = (
        await db.execute(
            select(UploadToken, Project)
            .join(Project, Project.id == UploadToken.project_id)
            .where(UploadToken.created_by_user_id == user.id)
        )
    ).all()
    events = (
        await db.execute(
            select(AuditLog).where(AuditLog.actor_user_id == user.id).order_by(AuditLog.at)
        )
    ).scalars()
    cred = await db.get(GithubCredential, user.id)

    data: dict[str, Any] = {
        "exported_at": _iso(datetime.now(UTC)),
        "user": {
            "id": user.id,
            "github_id": user.github_id,
            "login": user.login,
            "avatar_url": user.avatar_url,
            "consented_privacy_version": user.consented_privacy_version,
            "consented_at": _iso(user.consented_at),
            "last_synced_at": _iso(user.last_synced_at),
            "created_at": _iso(user.created_at),
        },
        "github_authorization": {
            "stored": cred is not None,
            "note": "GitHub tokens are stored encrypted and are never exported.",
            "access_expires_at": _iso(cred.access_expires_at) if cred else None,
            "refresh_expires_at": _iso(cred.refresh_expires_at) if cred else None,
        },
        "sessions": [
            {
                "id": s.id,
                "created_at": _iso(s.created_at),
                "last_seen_at": _iso(s.last_seen_at),
                "expires_at": _iso(s.expires_at),
            }
            for s in sessions
        ],
        "project_memberships": [
            {
                "project": p.full_name,
                "project_id": p.id,
                "role": m.role,
                "github_permission": m.github_permission,
                "synced_at": _iso(m.synced_at),
            }
            for m, p in memberships
        ],
        "upload_tokens_created": [
            {
                "id": t.id,
                "project": p.full_name,
                "name": t.name,
                "prefix": t.prefix,
                "created_at": _iso(t.created_at),
                "last_used_at": _iso(t.last_used_at),
            }
            for t, p in tokens
        ],
        "activity": [
            {
                "at": _iso(e.at),
                "action": e.action,
                "project_id": e.project_id,
                "target": e.target,
                "detail": e.detail,
            }
            for e in events
        ],
    }
    body = json.dumps(data, indent=2, sort_keys=False).encode()
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="polygraph-export.json"'},
    )


@router.delete("", status_code=204)
async def delete_account(
    request: Request,
    user: User = Depends(require_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Response:
    deleted = await delete_user(ctx, db, user)
    audit.record(db, "account.delete", detail={"projects_deleted": len(deleted)})
    await db.commit()
    clear_session_cookies(request, ctx)
    return Response(status_code=204)
