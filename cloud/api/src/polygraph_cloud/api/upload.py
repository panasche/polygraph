"""``POST /api/upload`` and the upload status/compare endpoints used by the Action."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ratelimit
from ..context import AppContext
from ..errors import ApiError, conflict, forbidden, not_found, rate_limited, too_large, unauthorized
from ..github import GitHubError
from ..ids import UPLOAD_TOKEN_PREFIX, new_id
from ..logs import get_logger
from ..models import Project, Report, UploadToken
from ..oidc import ActionsIdentity, OIDCError
from ..schemas import Compare, UploadAccepted, UploadStatus
from ..security import constant_time_equals, hash_ip, sha256_hex
from ..services.access import get_member, role_for
from ..services.ingest import (
    check_content_policy,
    create_report,
    decode_body,
    is_gzip,
    parse_results_async,
)
from ..services.processing import drain_inline
from ..services.queries import build_compare, check_report_id, resolve_base
from .deps import get_auth, get_ctx, get_db

log = get_logger(__name__)
router = APIRouter(prefix="/api/upload", tags=["upload"])


@dataclass
class Uploader:
    kind: str  # oidc | token
    identity: ActionsIdentity | None = None
    token: UploadToken | None = None


async def authenticate(request: Request, ctx: AppContext, db: AsyncSession) -> Uploader | None:
    """Read ``Authorization``. None when absent; raises 401 when present but bad."""
    value = request.headers.get("authorization")
    if not value:
        return None
    scheme, _, cred = value.partition(" ")
    cred = cred.strip()
    scheme = scheme.lower()
    if scheme == "bearer" and cred:
        try:
            identity = await ctx.oidc.verify(cred)
        except OIDCError as exc:
            raise unauthorized(str(exc)) from None
        return Uploader("oidc", identity=identity)
    if scheme == "token" and cred.startswith(UPLOAD_TOKEN_PREFIX) and len(cred) <= 128:
        digest = sha256_hex(cred)
        row = (
            await db.execute(select(UploadToken).where(UploadToken.token_hash == digest))
        ).scalar_one_or_none()
        if row is None or not constant_time_equals(row.token_hash, digest):
            raise unauthorized("Unknown or revoked upload token.")
        row.last_used_at = datetime.now(UTC)
        return Uploader("token", token=row)
    raise unauthorized("Use 'Authorization: Bearer <GitHub OIDC token>' or 'Token pgp_...'.")


async def _project_for_identity(db: AsyncSession, identity: ActionsIdentity) -> Project | None:
    project = (
        await db.execute(select(Project).where(Project.github_repo_id == identity.repository_id))
    ).scalar_one_or_none()
    if project is not None:
        return project
    by_name = (
        await db.execute(
            select(Project).where(Project.full_name_lower == identity.repository.lower())
        )
    ).scalar_one_or_none()
    if by_name is not None and by_name.github_repo_id is None:
        by_name.github_repo_id = identity.repository_id
        return by_name
    if by_name is not None:
        raise conflict(
            "A different repository with this name is already registered. "
            "Ask its admin to delete the old project."
        )
    return None


def _apply_identity(project: Project, identity: ActionsIdentity) -> None:
    """Keep the project in step with what GitHub's signed token says about the repo."""
    owner, name = identity.repository.split("/", 1)
    project.owner, project.name, project.full_name_lower = owner, name, identity.repository.lower()
    if not identity.is_public and not project.is_private:
        project.is_private = True
        project.public_results = False  # hide at once when a repo goes private
    elif identity.is_public and project.is_private:
        project.is_private = False  # stays hidden until an admin switches results on


async def _create_project(ctx: AppContext, db: AsyncSession, identity: ActionsIdentity) -> Project:
    owner, name = identity.repository.split("/", 1)
    installation_id = None
    if not identity.is_public:
        try:
            installation_id = await ctx.github.repo_installation_id(owner, name)
        except GitHubError:
            raise ApiError(
                503, "internal", "GitHub can't be reached right now. Try again."
            ) from None
        if installation_id is None:
            raise forbidden(
                "Private repositories need the Polygraph GitHub App installed before uploading."
            )
        from ..models import GithubInstallation

        if await db.get(GithubInstallation, installation_id) is None:
            db.add(GithubInstallation(id=installation_id, account_login=owner, account_type=None))
            await db.flush()
    project = Project(
        id=new_id("prj"),
        github_repo_id=identity.repository_id,
        owner=owner,
        name=name,
        full_name_lower=identity.repository.lower(),
        is_private=not identity.is_public,
        public_results=identity.is_public,
        allow_text_upload=False,
        default_branch="main",
        installation_id=installation_id,
    )
    db.add(project)
    await db.flush()
    return project


def _rate_key(repo_id: int | None, project_id: str | None) -> str:
    """One bucket per repository, whichever way the upload authenticated."""
    return f"upload:repo:{repo_id}" if repo_id is not None else f"upload:project:{project_id}"


async def _read_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > limit):
        raise too_large(f"Uploads are limited to {limit // (1024 * 1024)} MB compressed.")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise too_large(f"Uploads are limited to {limit // (1024 * 1024)} MB compressed.")
    return bytes(buf)


@router.post("", status_code=202, response_model=UploadAccepted)
async def upload(
    request: Request,
    background: BackgroundTasks,
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    s = ctx.settings
    ip_key = "upload:ip:" + hash_ip(s.secret("session_secret"), request.state.client_ip)
    allowed, retry = await ratelimit.hit(ctx.sessionmaker, ip_key, s.uploads_per_ip_per_hour)
    if not allowed:
        raise rate_limited(retry)

    uploader = await authenticate(request, ctx, db)
    if uploader is None:
        raise unauthorized("Uploads need a GitHub Actions OIDC token or an upload token.")

    project: Project | None
    if uploader.kind == "token":
        assert uploader.token is not None
        project = await db.get(Project, uploader.token.project_id)
        if project is None:
            raise unauthorized("Unknown or revoked upload token.")
        rate_key = _rate_key(project.github_repo_id, project.id)
    else:
        assert uploader.identity is not None
        project = await _project_for_identity(db, uploader.identity)
        rate_key = _rate_key(uploader.identity.repository_id, None)
    allowed, retry = await ratelimit.hit(ctx.sessionmaker, rate_key, s.uploads_per_project_per_hour)
    if not allowed:
        raise rate_limited(retry)

    raw = await _read_body(request, s.max_upload_bytes)
    encoding = request.headers.get("content-encoding")
    decoded = decode_body(raw, encoding, s.max_uncompressed_bytes)
    results = await parse_results_async(decoded, s.max_runs_per_file)

    if uploader.kind == "oidc":
        assert uploader.identity is not None
        if project is None:
            project = await _create_project(ctx, db, uploader.identity)
        else:
            _apply_identity(project, uploader.identity)
    assert project is not None
    check_content_policy(results, project)

    report = await create_report(
        ctx,
        db,
        project,
        results,
        decoded,
        source=uploader.kind,
        identity=uploader.identity,
        original_gz=raw if is_gzip(raw, encoding) else None,
    )
    report_id, project_id, full_name = report.id, project.id, project.full_name
    await db.commit()
    await ctx.queue.send({"report_id": report_id})
    if ctx.queue.inline:
        background.add_task(drain_inline, ctx)
    log.info("upload_accepted", report_id=report_id, project_id=project_id, source=uploader.kind)
    body = UploadAccepted(
        report_id=report_id,
        status="queued",
        url=f"{s.public_base_url}/app/{full_name}/reports/{report_id}",
    )
    return JSONResponse(body.model_dump(), status_code=202, background=background)


async def _report_for_caller(
    request: Request, ctx: AppContext, db: AsyncSession, report_id: str
) -> tuple[Report, Project]:
    check_report_id(report_id)
    uploader = await authenticate(request, ctx, db)
    report = await db.get(Report, report_id)
    project = await db.get(Project, report.project_id) if report else None
    if report is None or project is None:
        raise not_found("Report not found.")
    if uploader is not None:
        ok = (
            uploader.kind == "token"
            and uploader.token is not None
            and uploader.token.project_id == project.id
        ) or (
            uploader.kind == "oidc"
            and uploader.identity is not None
            and project.github_repo_id == uploader.identity.repository_id
        )
        if not ok:
            raise not_found("Report not found.")
        await db.commit()  # last_used_at
        return report, project
    auth = await get_auth(request, ctx, db)
    member = await get_member(db, project, auth.user, ctx.settings)
    if role_for(project, member) is None:
        raise not_found("Report not found.")
    return report, project


@router.get("/{report_id}", response_model=UploadStatus)
async def upload_status(
    report_id: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> UploadStatus:
    report, _ = await _report_for_caller(request, ctx, db, report_id)
    return UploadStatus(
        report_id=report.id, status=report.status, rejection_reason=report.rejection_reason
    )


@router.get("/{report_id}/compare", response_model=Compare)
async def upload_compare(
    report_id: str,
    request: Request,
    base: str = Query(default="branch:main", min_length=1, max_length=300),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Compare:
    report, project = await _report_for_caller(request, ctx, db, report_id)
    if report.status in ("queued", "processing"):
        raise conflict("The report is still processing.")
    base_r = await resolve_base(db, project, base, report)
    return await build_compare(db, project, report, base_r)
