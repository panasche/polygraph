"""Projects, reports, runs, compare, trends, branches and upload tokens."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from ..context import AppContext
from ..errors import ApiError, bad_request, conflict, not_found
from ..github import GitHubError
from ..ids import new_id, new_upload_token, token_display_prefix
from ..logs import get_logger
from ..models import (
    GithubCredential,
    Project,
    ProjectMember,
    Report,
    ReportPair,
    RunIndex,
    UploadToken,
    User,
    UTCDateTime,
)
from ..schemas import (
    BranchOut,
    Compare,
    Items,
    Page,
    ProjectOut,
    ProjectPatch,
    ReportDetail,
    ReportLite,
    TokenCreate,
    Trends,
    UploadTokenCreated,
    UploadTokenOut,
)
from ..security import sha256_hex
from ..services import audit
from ..services.access import require_admin, require_read
from ..services.deletion import delete_project, delete_report
from ..services.queries import build_compare, get_report, latest_for_projects, resolve_base
from ..services.serialize import (
    decode_cursor,
    encode_cursor,
    project_out,
    report_detail,
    report_lite,
    token_out,
    trend_point,
)
from ..services.sync import sync_user
from ..storage import run_key
from .deps import get_ctx, get_db, optional_user, require_consented_user, require_user

log = get_logger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

RUN_LIST_CAP = 2000
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _member_fresh_cutoff(ctx: AppContext) -> datetime:
    return datetime.now(UTC) - timedelta(hours=ctx.settings.membership_max_age_hours)


async def _visible_projects(
    db: AsyncSession, ctx: AppContext, user: User, after: str | None, limit: int
) -> list[tuple[Project, ProjectMember]]:
    q = (
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
    )
    if user.github_id >= 0:
        q = q.where(ProjectMember.synced_at >= _member_fresh_cutoff(ctx))
    if after:
        q = q.where(Project.full_name_lower > after)
    q = q.order_by(Project.full_name_lower).limit(limit + 1)
    return [(p, m) for p, m in (await db.execute(q)).all()]


async def _page_of_projects(
    db: AsyncSession, ctx: AppContext, user: User, cursor: str | None, limit: int
) -> Page[ProjectOut]:
    rows = await _visible_projects(db, ctx, user, decode_cursor(cursor), limit)
    more = len(rows) > limit
    rows = rows[:limit]
    latest = await latest_for_projects(db, [p for p, _ in rows])
    items = [project_out(p, m.role, latest.get(p.id)) for p, m in rows]
    nxt = encode_cursor(rows[-1][0].full_name_lower) if more and rows else None
    return Page[ProjectOut](items=items, next_cursor=nxt)


async def _try_sync(ctx: AppContext, db: AsyncSession, user: User) -> None:
    try:
        await sync_user(ctx, db, user)
        await db.commit()
    except GitHubError as exc:
        await db.rollback()
        log.info("auto_sync_skipped", status=exc.status)


@router.get("", response_model=Page[ProjectOut])
async def list_projects(
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(require_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Page[ProjectOut]:
    stale = user.last_synced_at is None or user.last_synced_at < datetime.now(UTC) - timedelta(
        minutes=ctx.settings.auto_sync_after_minutes
    )
    if not cursor and stale and user.github_id >= 0 and await db.get(GithubCredential, user.id):
        await _try_sync(ctx, db, user)
    return await _page_of_projects(db, ctx, user, cursor, limit)


@router.post("/sync", response_model=Page[ProjectOut])
async def sync_projects(
    user: User = Depends(require_consented_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Page[ProjectOut]:
    if user.github_id >= 0:
        try:
            await sync_user(ctx, db, user)
        except GitHubError as exc:
            await db.rollback()
            if exc.status == 401:
                raise ApiError(
                    401, "unauthorized", "Your GitHub authorization has expired. Sign in again."
                ) from None
            raise ApiError(503, "internal", "GitHub can't be reached right now.") from None
        audit.record(db, "projects.sync", actor_user_id=user.id)
        await db.commit()
    return await _page_of_projects(db, ctx, user, None, 100)


@router.get("/{owner}/{repo}", response_model=ProjectOut)
async def get_project(
    owner: str,
    repo: str,
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project, role = await require_read(db, owner, repo, user, ctx.settings)
    latest = await latest_for_projects(db, [project])
    return project_out(project, role, latest.get(project.id))


@router.patch("/{owner}/{repo}", response_model=ProjectOut)
async def patch_project(
    owner: str,
    repo: str,
    body: ProjectPatch,
    user: User = Depends(require_consented_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = await require_admin(db, owner, repo, user, ctx.settings)
    changes: dict[str, Any] = {}
    if body.public_results is not None:
        if body.public_results and project.is_private:
            raise bad_request("Results of a private repository can't be made public.")
        project.public_results = body.public_results
        changes["public_results"] = body.public_results
    if body.allow_text_upload is not None:
        project.allow_text_upload = body.allow_text_upload
        changes["allow_text_upload"] = body.allow_text_upload
    if changes:
        audit.record(
            db, "project.update", actor_user_id=user.id, project_id=project.id, detail=changes
        )
    await db.commit()
    latest = await latest_for_projects(db, [project])
    return project_out(project, "admin", latest.get(project.id))


@router.delete("/{owner}/{repo}", status_code=204)
async def remove_project(
    owner: str,
    repo: str,
    user: User = Depends(require_consented_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await require_admin(db, owner, repo, user, ctx.settings)
    pid = project.id
    await delete_project(ctx, db, project)
    audit.record(db, "project.delete", actor_user_id=user.id, project_id=pid)
    await db.commit()
    return Response(status_code=204)


@router.get("/{owner}/{repo}/branches", response_model=Items[BranchOut])
async def branches(
    owner: str,
    repo: str,
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Items[BranchOut]:
    project, _ = await require_read(db, owner, repo, user, ctx.settings)
    last = type_coerce(func.max(Report.created_at), UTCDateTime())
    rows = (
        await db.execute(
            select(Report.branch, last, func.count())
            .where(
                Report.project_id == project.id,
                Report.status == "ready",
                Report.branch.is_not(None),
            )
            .group_by(Report.branch)
            .order_by(func.max(Report.created_at).desc())
            .limit(500)
        )
    ).all()
    items = [BranchOut(name=n, last_report_at=t, reports=c) for n, t, c in rows]
    return Items[BranchOut](items=items)


@router.get("/{owner}/{repo}/trends", response_model=Trends)
async def trends(
    owner: str,
    repo: str,
    branch: str | None = Query(default=None, max_length=255),
    days: int = Query(default=90, ge=1, le=730),
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Trends:
    project, _ = await require_read(db, owner, repo, user, ctx.settings)
    branch = branch or project.default_branch
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        (
            await db.execute(
                select(Report)
                .where(
                    Report.project_id == project.id,
                    Report.status == "ready",
                    Report.branch == branch,
                    Report.created_at >= since,
                )
                .order_by(Report.created_at.desc(), Report.id.desc())
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    return Trends(branch=branch, points=[trend_point(r) for r in reversed(rows)])


@router.get("/{owner}/{repo}/reports", response_model=Page[ReportLite])
async def list_reports(
    owner: str,
    repo: str,
    branch: str | None = Query(default=None, max_length=255),
    pr: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=25, ge=1, le=100),
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Page[ReportLite]:
    project, _ = await require_read(db, owner, repo, user, ctx.settings)
    q = select(Report).where(Report.project_id == project.id)
    if branch:
        q = q.where(Report.branch == branch)
    if pr is not None:
        q = q.where(Report.pr_number == pr)
    after = decode_cursor(cursor)
    if after:
        q = q.where(Report.id < after)
    rows = (await db.execute(q.order_by(Report.id.desc()).limit(limit + 1))).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Page[ReportLite](
        items=[report_lite(r, project) for r in rows],
        next_cursor=encode_cursor(rows[-1].id) if more and rows else None,
    )


@router.get("/{owner}/{repo}/reports/{report_id}", response_model=ReportDetail)
async def get_report_detail(
    owner: str,
    repo: str,
    report_id: str,
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> ReportDetail:
    project, _ = await require_read(db, owner, repo, user, ctx.settings)
    report = await get_report(db, project, report_id)
    pairs = (
        (
            await db.execute(
                select(ReportPair)
                .where(ReportPair.report_id == report.id)
                .order_by(
                    ReportPair.bad.desc(), ReportPair.server, ReportPair.tool, ReportPair.fault
                )
            )
        )
        .scalars()
        .all()
    )
    runs = (
        (
            await db.execute(
                select(RunIndex)
                .where(RunIndex.report_id == report.id)
                .order_by(RunIndex.is_bad.desc(), RunIndex.precedence.desc(), RunIndex.ordinal)
                .limit(RUN_LIST_CAP)
            )
        )
        .scalars()
        .all()
    )
    return report_detail(report, project, list(pairs), list(runs))


@router.delete("/{owner}/{repo}/reports/{report_id}", status_code=204)
async def remove_report(
    owner: str,
    repo: str,
    report_id: str,
    user: User = Depends(require_consented_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await require_admin(db, owner, repo, user, ctx.settings)
    report = await get_report(db, project, report_id)
    await delete_report(ctx, db, report)
    audit.record(
        db, "report.delete", actor_user_id=user.id, project_id=project.id, target=report_id
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/{owner}/{repo}/reports/{report_id}/runs/{run_id}")
async def get_run(
    owner: str,
    repo: str,
    report_id: str,
    run_id: str,
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = await require_read(db, owner, repo, user, ctx.settings)
    report = await get_report(db, project, report_id)
    if not _RUN_ID.match(run_id) or await db.get(RunIndex, (report.id, run_id)) is None:
        raise not_found("Run not found.")
    raw = await ctx.storage.get(run_key(project.id, report.id, run_id))
    if raw is None:
        days = ctx.settings.run_retention_days
        raise not_found(
            f"Full run details are kept for {days} days. This run's details were removed."
        )
    run = json.loads(raw)
    run["report_id"] = report.id
    run["content_included"] = report.content_included
    return run


@router.get("/{owner}/{repo}/compare", response_model=Compare)
async def compare(
    owner: str,
    repo: str,
    base: str = Query(min_length=1, max_length=300),
    head: str = Query(min_length=1, max_length=64),
    user: User | None = Depends(optional_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Compare:
    project, _ = await require_read(db, owner, repo, user, ctx.settings)
    head_r = await get_report(db, project, head)
    base_r = await resolve_base(db, project, base, head_r)
    return await build_compare(db, project, head_r, base_r)


# ---- upload tokens --------------------------------------------------------------------


@router.get("/{owner}/{repo}/tokens", response_model=Items[UploadTokenOut])
async def list_tokens(
    owner: str,
    repo: str,
    user: User = Depends(require_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Items[UploadTokenOut]:
    project = await require_admin(db, owner, repo, user, ctx.settings)
    rows = (
        (
            await db.execute(
                select(UploadToken)
                .where(UploadToken.project_id == project.id)
                .order_by(UploadToken.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return Items[UploadTokenOut](items=[token_out(t) for t in rows])


@router.post("/{owner}/{repo}/tokens", response_model=UploadTokenCreated, status_code=201)
async def create_token(
    owner: str,
    repo: str,
    body: TokenCreate,
    user: User = Depends(require_consented_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> UploadTokenCreated:
    project = await require_admin(db, owner, repo, user, ctx.settings)
    count = (
        await db.execute(
            select(func.count())
            .select_from(UploadToken)
            .where(UploadToken.project_id == project.id)
        )
    ).scalar_one()
    if count >= ctx.settings.max_tokens_per_project:
        raise conflict("This project already has the maximum number of upload tokens.")
    token, row = issue_token(project, body.name.strip(), user)
    db.add(row)
    audit.record(db, "token.create", actor_user_id=user.id, project_id=project.id, target=row.id)
    await db.commit()
    return UploadTokenCreated(**token_out(row).model_dump(), token=token)


def issue_token(project: Project, name: str, user: User | None) -> tuple[str, UploadToken]:
    token = new_upload_token()
    row = UploadToken(
        id=new_id("tok"),
        project_id=project.id,
        name=name[:64],
        token_hash=sha256_hex(token),
        prefix=token_display_prefix(token),
        created_by_user_id=user.id if user else None,
        created_by_login=user.login if user else None,
        created_at=datetime.now(UTC),
    )
    return token, row


@router.delete("/{owner}/{repo}/tokens/{token_id}", status_code=204)
async def delete_token(
    owner: str,
    repo: str,
    token_id: str,
    user: User = Depends(require_consented_user),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await require_admin(db, owner, repo, user, ctx.settings)
    row = await db.get(UploadToken, token_id) if len(token_id) <= 32 else None
    if row is None or row.project_id != project.id:
        raise not_found("Token not found.")
    await db.delete(row)
    audit.record(db, "token.delete", actor_user_id=user.id, project_id=project.id, target=token_id)
    await db.commit()
    return Response(status_code=204)
