"""Async engine and session factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    url = settings.async_database_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1] if "///" in url else ""
        if path and path != ":memory:" and not path.startswith("file:"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"connect_args": {"timeout": 30}}
    else:
        kwargs.update(pool_size=10, max_overflow=10, pool_recycle=1800)
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly (tests and SQLite dev only; production uses Alembic)."""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
