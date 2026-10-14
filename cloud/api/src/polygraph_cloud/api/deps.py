"""Request dependencies: context, database session, signed-in user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..context import AppContext
from ..errors import ApiError, unauthorized
from ..ids import new_id
from ..models import Session, User
from ..security import csrf_for_session, new_session_token, sha256_hex
from .middleware import CSRF_COOKIE, SESSION_COOKIE, cookie_header, parse_cookies, queue_cookie

RENEW_AFTER = timedelta(hours=1)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


async def get_db(ctx: AppContext = Depends(get_ctx)) -> AsyncIterator[AsyncSession]:
    async with ctx.sessionmaker() as db:
        yield db


def request_cookies(request: Request) -> dict[str, str]:
    return parse_cookies(request.headers.get("cookie", ""))


def set_session_cookies(request: Request, ctx: AppContext, token: str) -> None:
    s = ctx.settings
    max_age = s.session_days * 86400
    state = request.scope.setdefault("state", {})
    queue_cookie(
        state, cookie_header(SESSION_COOKIE, token, max_age=max_age, secure=s.cookie_secure)
    )
    csrf = csrf_for_session(s.secret("session_secret"), token)
    queue_cookie(
        state,
        cookie_header(CSRF_COOKIE, csrf, max_age=max_age, secure=s.cookie_secure, http_only=False),
    )


def clear_session_cookies(request: Request, ctx: AppContext) -> None:
    s = ctx.settings
    state = request.scope.setdefault("state", {})
    queue_cookie(state, cookie_header(SESSION_COOKIE, "", max_age=0, secure=s.cookie_secure))
    queue_cookie(
        state, cookie_header(CSRF_COOKIE, "", max_age=0, secure=s.cookie_secure, http_only=False)
    )


async def start_session(request: Request, ctx: AppContext, db: AsyncSession, user: User) -> None:
    """Create a fresh session (old one, if any, is dropped: no session fixation)."""
    old = request_cookies(request).get(SESSION_COOKIE)
    if old:
        existing = await _find_session(db, old)
        if existing is not None:
            await db.delete(existing)
    token = new_session_token()
    now = datetime.now(UTC)
    db.add(
        Session(
            id=new_id("ses"),
            user_id=user.id,
            token_hash=sha256_hex(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=ctx.settings.session_days),
        )
    )
    set_session_cookies(request, ctx, token)


async def _find_session(db: AsyncSession, token: str) -> Session | None:
    from sqlalchemy import select

    if len(token) > 128:
        return None
    return (
        await db.execute(select(Session).where(Session.token_hash == sha256_hex(token)))
    ).scalar_one_or_none()


@dataclass
class Auth:
    user: User | None
    session: Session | None

    @property
    def consented(self) -> bool:
        return self.user is not None and self.user.consented_privacy_version is not None


async def get_auth(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
) -> Auth:
    cached = getattr(request.state, "pg_auth", None)
    if cached is not None:
        return cached
    token = request_cookies(request).get(SESSION_COOKIE)
    auth = Auth(None, None)
    if token:
        session = await _find_session(db, token)
        now = datetime.now(UTC)
        if session is not None and session.expires_at > now:
            user = await db.get(User, session.user_id)
            if user is not None:
                auth = Auth(user, session)
                if now - session.last_seen_at > RENEW_AFTER:  # 30-day sliding window
                    session.last_seen_at = now
                    session.expires_at = now + timedelta(days=ctx.settings.session_days)
                    await db.commit()
                    set_session_cookies(request, ctx, token)
        elif session is not None:
            await db.delete(session)
            await db.commit()
    request.state.pg_auth = auth
    return auth


async def optional_user(auth: Auth = Depends(get_auth)) -> User | None:
    return auth.user


async def require_user(auth: Auth = Depends(get_auth)) -> User:
    if auth.user is None:
        raise unauthorized()
    return auth.user


async def require_consented_user(
    auth: Auth = Depends(get_auth), ctx: AppContext = Depends(get_ctx)
) -> User:
    """Changes need the current privacy notice accepted (DPDP Act consent)."""
    if auth.user is None:
        raise unauthorized()
    if auth.user.consented_privacy_version != ctx.settings.privacy_version:
        raise ApiError(
            403,
            "consent_required",
            "Please read and accept the current privacy notice first.",
        )
    return auth.user
