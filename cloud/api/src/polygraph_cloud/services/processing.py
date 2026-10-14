"""The worker pipeline (CONTRACTS.md 5).

load raw -> ``polygraph.results.parse`` -> content policy -> PII scan ->
recompute summary with ``polygraph.metrics.summarize`` (the uploaded summary is
ignored) -> write per-run files -> write metrics, heatmap and run index ->
status ``ready`` (or ``rejected`` with a reason).

Processing is idempotent: a report is claimed with a conditional update, all
derived rows are replaced in one transaction, and run files have fixed keys.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import anyio.to_thread
from sqlalchemy import and_, delete, insert, or_, select, update

from polygraph import results as pr
from polygraph.metrics import summarize

from ..context import AppContext
from ..errors import ApiError
from ..logs import get_logger
from ..models import Project, Report, ReportPair, RunIndex
from ..pii import scan_results
from ..storage import run_key
from .ingest import TooLarge, gunzip_capped, parse_results

log = get_logger(__name__)

BAD = {o.value for o in pr.BAD_OUTCOMES}


def recompute_coverage(results: pr.ResultsFile) -> pr.Coverage:
    """Coverage from the runs themselves when the file lists its pairs."""
    cov = results.coverage
    if not cov.pairs:
        return pr.Coverage(applicable=cov.applicable, covered=min(cov.covered, cov.applicable))
    done: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in results.runs:
        if r.fault is not None:
            done[(r.fault.server, r.fault.tool, r.fault.name.value)] += 1
    pairs = []
    for p in cov.pairs:
        pairs.append(p.model_copy(update={"runs_done": done[(p.server, p.tool, p.fault.value)]}))
    covered = sum(1 for p in pairs if p.covered)
    return pr.Coverage(applicable=len(pairs), covered=covered, pairs=pairs)


def heatmap(runs: list[pr.Run]) -> list[dict[str, Any]]:
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in runs:
        if r.fault is None:
            continue
        key = (r.fault.server, r.fault.tool, r.fault.name.value)
        cell = cells.setdefault(
            key,
            {
                "server": key[0],
                "tool": key[1],
                "fault": key[2],
                "runs": 0,
                "bad": 0,
                "outcomes": {},
            },
        )
        cell["runs"] += 1
        cell["outcomes"][r.outcome.value] = cell["outcomes"].get(r.outcome.value, 0) + 1
        if r.outcome.value in BAD:
            cell["bad"] += 1
    return sorted(cells.values(), key=lambda c: (-c["bad"], c["server"], c["tool"], c["fault"]))


def run_rows(report_id: str, runs: list[pr.Run]) -> list[dict[str, Any]]:
    rows = []
    for i, r in enumerate(runs):
        rows.append(
            {
                "report_id": report_id,
                "run_id": r.id,
                "ordinal": i,
                "test_id": r.test_id[:1024],
                "task": r.task[:1024] if r.task else None,
                "fault": r.fault.model_dump(mode="json") if r.fault else None,
                "fault_name": r.fault.name.value if r.fault else None,
                "repeat": r.repeat,
                "outcome": r.outcome.value,
                "is_bad": r.outcome.value in BAD,
                "precedence": pr.OUTCOME_PRECEDENCE[r.outcome],
                "reasons": {"items": [x[:500] for x in r.reasons[:20]]},
                "duration_ms": r.duration_ms,
            }
        )
    return rows


async def _write_run_files(
    ctx: AppContext, project_id: str, report_id: str, runs: list[pr.Run]
) -> None:
    limiter = anyio.Semaphore(16)

    async def put(run: pr.Run) -> None:
        async with limiter:
            await ctx.storage.put(
                run_key(project_id, report_id, run.id), run.model_dump_json().encode()
            )

    async with anyio.create_task_group() as tg:
        for run in runs:
            tg.start_soon(put, run)


async def reject(ctx: AppContext, report_id: str, reason: str) -> None:
    """Mark rejected and delete the raw upload (nothing rejected is kept)."""
    async with ctx.sessionmaker() as db:
        report = await db.get(Report, report_id)
        if report is None:
            return
        if report.raw_key:
            try:
                await ctx.storage.delete(report.raw_key)
            except Exception as exc:  # the prune job retries orphans later
                log.warning("raw_delete_failed", report_id=report_id, error_type=type(exc).__name__)
        report.status = "rejected"
        report.rejection_reason = reason[:500]
        report.raw_key = None
        report.processed_at = datetime.now(UTC)
        await db.commit()
    log.info("report_rejected", report_id=report_id)


async def claim(ctx: AppContext, report_id: str) -> str:
    """Move a report to ``processing``. Returns claimed | done | busy | missing."""
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=ctx.settings.sqs_visibility_timeout_s)
    async with ctx.sessionmaker() as db:
        res = await db.execute(
            update(Report)
            .where(
                Report.id == report_id,
                or_(
                    Report.status == "queued",
                    and_(Report.status == "processing", Report.processing_started_at < stale),
                ),
            )
            .values(status="processing", processing_started_at=now, attempts=Report.attempts + 1)
        )
        await db.commit()
        if res.rowcount:
            return "claimed"
        status = (
            await db.execute(select(Report.status).where(Report.id == report_id))
        ).scalar_one_or_none()
    if status is None:
        return "missing"
    if status in ("ready", "rejected"):
        return "done"
    return "busy"


async def release(ctx: AppContext, report_id: str) -> None:
    """Hand a report back to the queue after a transient failure."""
    async with ctx.sessionmaker() as db:
        await db.execute(
            update(Report)
            .where(Report.id == report_id, Report.status == "processing")
            .values(status="queued")
        )
        await db.commit()


async def process_report(ctx: AppContext, report_id: str) -> str:
    """Process one report. Returns ready | rejected | done | busy | missing.

    Raises on transient failures (storage or database errors) after handing the
    report back, so the queue can retry it.
    """
    state = await claim(ctx, report_id)
    if state != "claimed":
        return state
    try:
        return await _process(ctx, report_id)
    except Exception:
        await release(ctx, report_id)
        raise


async def _process(ctx: AppContext, report_id: str) -> str:
    s = ctx.settings
    async with ctx.sessionmaker() as db:
        report = await db.get(Report, report_id)
        assert report is not None
        project = await db.get(Project, report.project_id)
        assert project is not None
        raw_key_ = report.raw_key
        project_id = project.id
        allow_text = project.allow_text_upload

    raw = await ctx.storage.get(raw_key_) if raw_key_ else None
    if raw is None:
        await reject(ctx, report_id, "The uploaded file is missing. Upload it again.")
        return "rejected"
    try:
        data = await anyio.to_thread.run_sync(gunzip_capped, raw, s.max_uncompressed_bytes)
    except TooLarge:
        await reject(ctx, report_id, "The results file is too large once uncompressed.")
        return "rejected"
    except Exception:
        await reject(ctx, report_id, "The stored upload is not valid gzip.")
        return "rejected"
    try:
        results = await anyio.to_thread.run_sync(parse_results, data, s.max_runs_per_file)
    except ApiError as exc:
        await reject(ctx, report_id, exc.message)
        return "rejected"
    del data, raw

    if results.content_included:
        if not allow_text:
            await reject(
                ctx,
                report_id,
                "This upload includes payload text, but the project doesn't allow text upload.",
            )
            return "rejected"
        finding = await anyio.to_thread.run_sync(scan_results, results, ctx.scanner)
        if finding is not None:
            await reject(ctx, report_id, finding.message())
            return "rejected"

    coverage = recompute_coverage(results)
    summary = summarize(results.runs, coverage)  # never the uploaded summary
    cells = heatmap(results.runs)
    rows = run_rows(report_id, results.runs)

    await _write_run_files(ctx, project_id, report_id, results.runs)

    now = datetime.now(UTC)
    async with ctx.sessionmaker() as db:
        report = await db.get(Report, report_id)
        if report is None:  # deleted while we worked
            return "missing"
        await db.execute(delete(ReportPair).where(ReportPair.report_id == report_id))
        await db.execute(delete(RunIndex).where(RunIndex.report_id == report_id))
        if cells:
            await db.execute(insert(ReportPair), [{"report_id": report_id, **c} for c in cells])
        for i in range(0, len(rows), 1000):
            await db.execute(insert(RunIndex), rows[i : i + 1000])

        report.runs = summary.runs
        report.faulted_runs = summary.faulted_runs
        for name in ("success", "silent_lie", "double_action", "hijack"):
            r = getattr(summary, name)
            setattr(report, f"{name}_num", r.numerator)
            setattr(report, f"{name}_den", r.denominator)
        report.coverage_applicable = coverage.applicable
        report.coverage_covered = coverage.covered
        report.summary = summary.model_dump(mode="json")
        report.environment = results.environment.model_dump(mode="json")
        report.coverage_detail = coverage.model_dump(mode="json")
        report.tools = {"items": [t.model_dump(mode="json") for t in results.tools]}
        report.server_claims = {"items": [c.model_dump(mode="json") for c in results.server_claims]}
        report.content_included = results.content_included
        report.status = "ready"
        report.rejection_reason = None
        report.processed_at = now

        project = await db.get(Project, project_id)
        if project is not None and (
            project.last_report_at is None or project.last_report_at < report.created_at
        ):
            project.last_report_at = report.created_at
        await db.commit()
    log.info("report_ready", report_id=report_id, runs=summary.runs)
    return "ready"


async def drain_inline(ctx: AppContext) -> None:
    """Process everything waiting in the inline queue (development and tests)."""
    from ..worker import handle_message

    while True:
        messages = await ctx.queue.receive(10)
        if not messages:
            return
        for m in messages:
            await handle_message(ctx, m)
