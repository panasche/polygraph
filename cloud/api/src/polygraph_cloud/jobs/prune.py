"""Daily retention job: ``python -m polygraph_cloud.jobs.prune [--dry-run]``.

- Full run files (and the raw upload) are deleted after ``RUN_RETENTION_DAYS`` (90).
- Report rows (summary numbers, heatmap, run index) are deleted after
  ``REPORT_RETENTION_DAYS`` (730, about 2 years), with any storage left over.
- Expired sessions, OAuth states and old rate-limit windows are removed.
- Reports stuck in ``queued``/``processing`` for a day are marked rejected.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update

from .. import runtime
from ..config import get_settings
from ..context import AppContext, build_context
from ..logs import configure_logging, get_logger
from ..models import OAuthState, RateLimitCounter, Report, Session
from ..services.deletion import delete_report_objects
from ..storage import runs_prefix

log = get_logger("polygraph_cloud.prune")
BATCH = 200


@dataclass
class PruneStats:
    run_files_reports: int = 0
    reports_deleted: int = 0
    sessions_deleted: int = 0
    oauth_states_deleted: int = 0
    rate_windows_deleted: int = 0
    stuck_rejected: int = 0


async def prune(ctx: AppContext, now: datetime | None = None, dry_run: bool = False) -> PruneStats:
    s = ctx.settings
    now = now or datetime.now(UTC)
    stats = PruneStats()
    run_cutoff = now - timedelta(days=s.run_retention_days)
    report_cutoff = now - timedelta(days=s.report_retention_days)

    # 1. Old reports: delete everything.
    while True:
        async with ctx.sessionmaker() as db:
            rows = (
                (
                    await db.execute(
                        select(Report).where(Report.created_at < report_cutoff).limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            stats.reports_deleted += len(rows)
            if dry_run:
                break
            for r in rows:
                await delete_report_objects(ctx, r)
                await db.delete(r)
            await db.commit()

    # 2. Run files older than the run retention.
    while True:
        async with ctx.sessionmaker() as db:
            rows = (
                (
                    await db.execute(
                        select(Report)
                        .where(Report.created_at < run_cutoff, Report.runs_purged_at.is_(None))
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            stats.run_files_reports += len(rows)
            if dry_run:
                break
            for r in rows:
                await ctx.storage.delete_prefix(runs_prefix(r.project_id, r.id))
                if r.raw_key:
                    await ctx.storage.delete(r.raw_key)
                r.raw_key = None
                r.runs_purged_at = now
            await db.commit()

    async with ctx.sessionmaker() as db:
        if not dry_run:
            res = await db.execute(delete(Session).where(Session.expires_at < now))
            stats.sessions_deleted = res.rowcount or 0
            res = await db.execute(delete(OAuthState).where(OAuthState.expires_at < now))
            stats.oauth_states_deleted = res.rowcount or 0
            window_cutoff = int(time.time()) - 2 * 86400
            res = await db.execute(
                delete(RateLimitCounter).where(RateLimitCounter.window_start < window_cutoff)
            )
            stats.rate_windows_deleted = res.rowcount or 0
            stuck = await db.execute(
                update(Report)
                .where(
                    or_(Report.status == "queued", Report.status == "processing"),
                    Report.created_at < now - timedelta(days=1),
                )
                .values(
                    status="rejected",
                    rejection_reason="Processing did not finish. Upload it again.",
                    processed_at=now,
                )
            )
            stats.stuck_rejected = stuck.rowcount or 0
            await db.commit()
    log.info("prune_done", dry_run=dry_run, **asdict(stats))
    return stats


async def _main(dry_run: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.check()
    ctx = build_context(settings)
    try:
        stats = await prune(ctx, dry_run=dry_run)
        print(asdict(stats))
    finally:
        await ctx.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m polygraph_cloud.jobs.prune")
    parser.add_argument("--dry-run", action="store_true", help="count only, delete nothing")
    args = parser.parse_args(argv)
    return runtime.run(_main, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
