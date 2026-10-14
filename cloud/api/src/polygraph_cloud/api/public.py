"""Public, cache-friendly endpoints: badge, leaderboard, health and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from polygraph.results import PassK

from .. import badge
from ..context import AppContext
from ..logs import get_logger
from ..models import Report
from ..schemas import Leaderboard, LeaderboardEntry
from ..services.access import find_project, publicly_visible
from ..services.queries import latest_ready
from ..services.serialize import rate
from .deps import get_ctx, get_db

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["public"])

PUBLIC_CACHE = "public, max-age=300"


def _svg(data: badge.BadgeData | None) -> Response:
    return Response(
        content=badge.render(data),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": PUBLIC_CACHE,
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@router.get("/badge/{owner}/{repo_svg}", response_class=Response)
async def badge_svg(
    owner: str,
    repo_svg: str,
    branch: str | None = Query(default=None, max_length=255),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # Private and unknown projects get the very same neutral badge.
    if not repo_svg.endswith(".svg"):
        return _svg(None)
    project = await find_project(db, owner, repo_svg[: -len(".svg")])
    if project is None or not publicly_visible(project):
        return _svg(None)
    report = await latest_ready(db, project.id, branch or project.default_branch)
    if report is None:
        return _svg(None)
    sl, da = rate(report, "silent_lie"), rate(report, "double_action")
    return _svg(
        badge.BadgeData(
            silent_lie=sl.value,
            double_action=da.value,
            covered=report.coverage_covered,
            applicable=report.coverage_applicable,
        )
    )


@router.get("/leaderboard", response_model=Leaderboard)
async def leaderboard(
    ctx: AppContext = Depends(get_ctx), db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    name = ctx.settings.leaderboard_project
    owner, _, repo = name.partition("/")
    project = await find_project(db, owner, repo) if repo else None
    entries: list[LeaderboardEntry] = []
    updated_at = None
    if project is not None and publicly_visible(project):
        rows = (
            await db.execute(
                select(Report)
                .where(Report.project_id == project.id, Report.status == "ready")
                .order_by(Report.created_at.desc(), Report.id.desc())
                .limit(2000)
            )
        ).scalars()
        seen: set[tuple[Any, ...]] = set()
        for r in rows:
            key = (r.framework, r.config_label, r.model)
            if key in seen:
                continue
            seen.add(key)
            pk = (r.summary or {}).get("pass_k")
            entries.append(
                LeaderboardEntry(
                    framework=r.framework,
                    framework_version=r.framework_version,
                    config_label=r.config_label,
                    model=r.model,
                    report_id=r.id,
                    runs=r.runs,
                    success=rate(r, "success"),
                    silent_lie=rate(r, "silent_lie"),
                    double_action=rate(r, "double_action"),
                    hijack=rate(r, "hijack"),
                    pass_k=PassK.model_validate(pk) if pk else None,
                )
            )
            if updated_at is None or r.created_at > updated_at:
                updated_at = r.created_at
        entries.sort(
            key=lambda e: (
                e.silent_lie.value if e.silent_lie.value is not None else 2.0,
                e.framework or "",
                e.config_label or "",
            )
        )
    body = Leaderboard(project=name, updated_at=updated_at, entries=entries)
    return JSONResponse(body.model_dump(mode="json"), headers={"Cache-Control": PUBLIC_CACHE})


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(ctx: AppContext = Depends(get_ctx)) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with ctx.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("ready_db_failed", error_type=type(exc).__name__)
        checks["database"] = "unavailable"
    for name, probe in (("storage", ctx.storage.ping), ("queue", ctx.queue.ping)):
        try:
            await probe()
            checks[name] = "ok"
        except Exception as exc:
            log.warning(f"ready_{name}_failed", error_type=type(exc).__name__)
            checks[name] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503
    )
