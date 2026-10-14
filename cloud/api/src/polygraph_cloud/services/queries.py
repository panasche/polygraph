"""Read-side queries shared by several routes."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import bad_request, conflict, not_found
from ..models import Project, Report, ReportPair
from ..schemas import Compare as CompareOut
from ..schemas import (
    ComparePair,
    CoveragePairOut,
    MetricPair,
    PairSide,
    ReportLite,
)
from .serialize import coverage, rate, report_lite

REPORT_ID = re.compile(r"^rep_[0-9a-z]{26}$")


def check_report_id(report_id: str) -> str:
    if not REPORT_ID.match(report_id):
        raise not_found("Report not found.")
    return report_id


async def get_report(db: AsyncSession, project: Project, report_id: str) -> Report:
    check_report_id(report_id)
    report = await db.get(Report, report_id)
    if report is None or report.project_id != project.id:
        raise not_found("Report not found.")
    return report


async def latest_ready(
    db: AsyncSession, project_id: str, branch: str | None, exclude: str | None = None
) -> Report | None:
    q = select(Report).where(Report.project_id == project_id, Report.status == "ready")
    if branch is not None:
        q = q.where(Report.branch == branch)
    if exclude:
        q = q.where(Report.id != exclude)
    q = q.order_by(Report.created_at.desc(), Report.id.desc()).limit(1)
    return (await db.execute(q)).scalar_one_or_none()


async def latest_for_projects(db: AsyncSession, projects: list[Project]) -> dict[str, ReportLite]:
    """Latest ready report per project: on its default branch, else on any branch."""
    if not projects:
        return {}
    ids = [p.id for p in projects]
    newest = (
        select(func.max(Report.id))
        .where(Report.project_id.in_(ids), Report.status == "ready")
        .group_by(Report.project_id, Report.branch)
    )
    rows = (await db.execute(select(Report).where(Report.id.in_(newest)))).scalars().all()
    by_project: dict[str, list[Report]] = {}
    for r in rows:
        by_project.setdefault(r.project_id, []).append(r)
    out = {}
    for p in projects:
        cands = by_project.get(p.id, [])
        if not cands:
            continue
        on_default = [r for r in cands if r.branch == p.default_branch]
        pick = max(on_default or cands, key=lambda r: r.id)
        out[p.id] = report_lite(pick, p)
    return out


async def resolve_base(db: AsyncSession, project: Project, spec: str, head: Report) -> Report:
    spec = spec.strip()
    if spec.startswith("branch:"):
        branch = spec[len("branch:") :]
        if not branch or len(branch) > 255:
            raise bad_request("Invalid base branch.")
        base = await latest_ready(db, project.id, branch, exclude=head.id)
        if base is None:
            raise not_found(f"No ready report on branch {branch}.")
        return base
    return await get_report(db, project, spec)


async def build_compare(
    db: AsyncSession, project: Project, head: Report, base: Report
) -> CompareOut:
    for r, label in ((head, "head"), (base, "base")):
        if r.status in ("queued", "processing"):
            raise conflict(f"The {label} report is still processing.")
        if r.status != "ready":
            raise conflict(f"The {label} report was rejected.")
    pairs = (
        (await db.execute(select(ReportPair).where(ReportPair.report_id.in_([base.id, head.id]))))
        .scalars()
        .all()
    )
    cells: dict[tuple[str, str, str], dict[str, PairSide]] = {}
    for p in pairs:
        side = "base" if p.report_id == base.id else "head"
        cells.setdefault((p.server, p.tool, p.fault), {})[side] = PairSide(runs=p.runs, bad=p.bad)
    if base.id == head.id:
        for v in cells.values():
            v["base"] = v["head"]

    def sort_key(item: tuple[tuple[str, str, str], dict[str, PairSide]]) -> tuple:
        (server, tool, fault), sides = item
        h, b = sides.get("head"), sides.get("base")
        delta = (h.bad if h else 0) - (b.bad if b else 0)
        return (-delta, -(h.bad if h else 0), server, tool, fault)

    out_pairs = [
        ComparePair(server=k[0], tool=k[1], fault=k[2], base=v.get("base"), head=v.get("head"))
        for k, v in sorted(cells.items(), key=sort_key)
    ]
    return CompareOut(
        base=report_lite(base, project),
        head=report_lite(head, project),
        metrics={
            m: MetricPair(base=rate(base, m), head=rate(head, m))
            for m in ("silent_lie", "double_action", "hijack", "success")
        },
        coverage=CoveragePairOut(base=coverage(base), head=coverage(head)),
        pairs=out_pairs,
    )
