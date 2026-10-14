"""Sign-in with the GitHub App (user authorization flow + PKCE), logout, dev-login."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..context import AppContext
from ..errors import ApiError
from ..github import GitHubError
from ..ids import new_id
from ..logs import get_logger
from ..models import OAuthState, Project, ProjectMember, User
from ..schemas import DevLoginIn, UserOut
from ..security import constant_time_equals, pkce_pair, sha256_hex
from ..services import audit
from ..services.serialize import user_out
from ..services.sync import store_tokens, sync_user
from .deps import (
    clear_session_cookies,
    get_auth,
    get_ctx,
    get_db,
    request_cookies,
    start_session,
)
from .middleware import STATE_COOKIE, cookie_header, queue_cookie

log = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

STATE_TTL_S = 600
STATE_PATH = "/api/auth/github"
_NEXT_OK = re.compile(r"^/[A-Za-z0-9/_\-.~%?=&]*$")


def safe_next(value: str | None) -> str:
    """Only same-site relative paths. Blocks open redirects like ``//evil`` or ``/\\evil``."""
    if not value or len(value) > 512 or not _NEXT_OK.match(value) or value.startswith("//"):
        return "/app"
    return value


def _fail(ctx: AppContext, request: Request, code: str) -> RedirectResponse:
    queue_cookie(
        request.scope.setdefault("state", {}),
        cookie_header(
            STATE_COOKIE, "", max_age=0, secure=ctx.settings.cookie_secure, path=STATE_PATH
        ),
    )
    return RedirectResponse(f"{ctx.settings.public_base_url}/signin?error={quote(code)}", 302)


@router.get("/github/login", include_in_schema=True)
async def github_login(
    request: Request,
    next: str | None = Query(default=None, max_length=512),  # noqa: A002
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if not ctx.settings.github_oauth_configured:
        raise ApiError(503, "internal", "GitHub sign-in isn't configured on this server.")
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    now = datetime.now(UTC)
    await db.execute(delete(OAuthState).where(OAuthState.expires_at < now))
    db.add(
        OAuthState(
            state_hash=sha256_hex(state),
            code_verifier=verifier,
            next_path=safe_next(next),
            created_at=now,
            expires_at=now + timedelta(seconds=STATE_TTL_S),
        )
    )
    await db.commit()
    queue_cookie(
        request.scope.setdefault("state", {}),
        cookie_header(
            STATE_COOKIE,
            ctx.state_signer.sign(state),
            max_age=STATE_TTL_S,
            secure=ctx.settings.cookie_secure,
            path=STATE_PATH,
        ),
    )
    return RedirectResponse(ctx.github.authorize_url(state, challenge), 302)


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = Query(default=None, max_length=256),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=128),
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if error:
        return _fail(ctx, request, "access_denied")
    if not code or not state:
        return _fail(ctx, request, "bad_request")
    signed = request_cookies(request).get(STATE_COOKIE)
    cookie_state = ctx.state_signer.unsign(signed, STATE_TTL_S) if signed else None
    if not constant_time_equals(cookie_state, state):
        return _fail(ctx, request, "state_mismatch")
    row = await db.get(OAuthState, sha256_hex(state))
    if row is None or row.expires_at < datetime.now(UTC):
        return _fail(ctx, request, "state_expired")
    verifier, next_path = row.code_verifier, row.next_path
    await db.delete(row)  # one-time use
    await db.commit()

    try:
        tokens = await ctx.github.exchange_code(code, verifier)
        gh_user = await ctx.github.get_user(tokens.access_token)
    except GitHubError:
        return _fail(ctx, request, "github_error")

    user = (await db.execute(select(User).where(User.github_id == gh_user.id))).scalar_one_or_none()
    if user is None:
        user = User(id=new_id("usr"), github_id=gh_user.id, login=gh_user.login)
        db.add(user)
    user.login = gh_user.login[:64]
    user.avatar_url = (gh_user.avatar_url or "")[:512] or None
    await db.flush()
    await store_tokens(ctx, db, user.id, tokens)
    await db.flush()
    try:
        await sync_user(ctx, db, user)
    except GitHubError as exc:
        log.warning("installation_sync_failed", status=exc.status)
    await start_session(request, ctx, db, user)
    audit.record(db, "auth.login", actor_user_id=user.id)
    await db.commit()
    queue_cookie(
        request.scope.setdefault("state", {}),
        cookie_header(
            STATE_COOKIE, "", max_age=0, secure=ctx.settings.cookie_secure, path=STATE_PATH
        ),
    )
    return RedirectResponse(f"{ctx.settings.public_base_url}{next_path}", 302)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Response:
    auth = await get_auth(request, ctx, db)
    if auth.session is not None:
        audit.record(db, "auth.logout", actor_user_id=auth.session.user_id)
        await db.delete(auth.session)
        await db.commit()
    clear_session_cookies(request, ctx)
    return Response(status_code=204)


def _dev_github_id(login: str) -> int:
    # Negative ids can never collide with real GitHub users.
    return -int.from_bytes(hashlib.sha256(login.lower().encode()).digest()[:6], "big") - 1


async def make_dev_admin_everywhere(db: AsyncSession, user: User) -> None:
    projects = (await db.execute(select(Project.id))).scalars().all()
    now = datetime.now(UTC)
    for pid in projects:
        m = await db.get(ProjectMember, (pid, user.id))
        if m is None:
            db.add(
                ProjectMember(
                    project_id=pid,
                    user_id=user.id,
                    role="admin",
                    github_permission="admin",
                    synced_at=now,
                )
            )
        else:
            m.role, m.synced_at = "admin", now


dev_router = APIRouter(prefix="/api/auth", tags=["auth"])


@dev_router.post("/dev-login", response_model=UserOut)
async def dev_login(
    request: Request,
    body: DevLoginIn | None = None,
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    if not ctx.settings.is_dev:  # belt and braces: the route isn't mounted outside dev
        raise ApiError(404, "not_found", "Not found.")
    login = (body or DevLoginIn()).login
    gid = _dev_github_id(login)
    user = (await db.execute(select(User).where(User.github_id == gid))).scalar_one_or_none()
    if user is None:
        user = User(id=new_id("usr"), github_id=gid, login=login, avatar_url=None)
        db.add(user)
        await db.flush()
    await make_dev_admin_everywhere(db, user)
    await start_session(request, ctx, db, user)
    audit.record(db, "auth.dev_login", actor_user_id=user.id)
    await db.commit()
    return user_out(user)
