"""Fixed-window rate limits stored in the database (a WAF sits in front as well)."""

from __future__ import annotations

import time

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import RateLimitCounter


async def hit(
    sessionmaker: async_sessionmaker[AsyncSession],
    key: str,
    limit: int,
    window_s: int = 3600,
    now: float | None = None,
) -> tuple[bool, int]:
    """Count one event. Returns ``(allowed, retry_after_seconds)``.

    Runs in its own short transaction so the count sticks even when the
    request that triggered it fails later.
    """
    ts = time.time() if now is None else now
    window = int(ts // window_s) * window_s
    async with sessionmaker() as db:
        dialect = db.bind.dialect.name
        ins_mod = postgresql if dialect == "postgresql" else sqlite
        stmt = ins_mod.insert(RateLimitCounter).values(key=key, window_start=window, count=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key", "window_start"],
            set_={"count": RateLimitCounter.count + 1},
        ).returning(RateLimitCounter.count)
        count = (await db.execute(stmt)).scalar_one()
        await db.commit()
    retry_after = int(window + window_s - ts) + 1
    return count <= limit, retry_after
