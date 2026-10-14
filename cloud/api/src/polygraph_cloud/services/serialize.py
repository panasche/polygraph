"""Turn rows into API shapes."""

from __future__ import annotations

import base64
import json
from typing import Any

from polygraph.metrics import wilson
from polygraph.results import Rate

from ..errors import bad_request
from ..models import Project, Report, ReportPair, RunIndex, UploadToken, User
from ..schemas import (
    CoverageCount,
    HeatCell,
    ProjectOut,
    ReportDetail,
    ReportLite,
    RunRow,
    TrendPoint,
    UploadTokenOut,
    UserOut,
)

METRICS = ("success", "silent_lie", "double_action", "hijack")


def rate(report: Report, metric: str) -> Rate:
    return wilson(getattr(report, f"{metric}_num"), getattr(report, f"{metric}_den"))


def coverage(report: Report) -> CoverageCount:
    return CoverageCount(applicable=report.coverage_applicable, covered=report.coverage_covered)


def user_out(user: User) -> UserOut:
    return UserOut.model_validate(user)


def report_lite(report: Report, project: Project) -> ReportLite:
    return ReportLite(
        id=report.id,
        project=project.full_name,
        status=report.status,  # type: ignore[arg-type]
        rejection_reason=report.rejection_reason,
        source=report.source,  # type: ignore[arg-type]
        branch=report.branch,
        sha=report.sha,
        pr_number=report.pr_number,
        created_at=report.created_at,
        framework=report.framework,
        model=report.model,
        config_label=report.config_label,
        runs=report.runs,
        faulted_runs=report.faulted_runs,
        coverage=coverage(report),
        **{m: rate(report, m) for m in METRICS},
    )


def heat_cell(p: ReportPair) -> HeatCell:
    return HeatCell(
        server=p.server, tool=p.tool, fault=p.fault, runs=p.runs, bad=p.bad, outcomes=p.outcomes
    )


def run_row(r: RunIndex) -> RunRow:
    return RunRow(
        id=r.run_id,
        test_id=r.test_id,
        task=r.task,
        fault=r.fault,
        repeat=r.repeat,
        outcome=r.outcome,
        reasons=list((r.reasons or {}).get("items", [])),
        duration_ms=r.duration_ms,
    )


def report_detail(
    report: Report, project: Project, pairs: list[ReportPair], runs: list[RunIndex]
) -> ReportDetail:
    lite = report_lite(report, project)
    return ReportDetail(
        **lite.model_dump(),
        environment=report.environment,
        summary=report.summary,
        coverage_detail=report.coverage_detail,
        tools=list((report.tools or {}).get("items", [])),
        server_claims=list((report.server_claims or {}).get("items", [])),
        content_included=report.content_included,
        heatmap=[heat_cell(p) for p in pairs],
        run_list=[run_row(r) for r in runs],
    )


def trend_point(report: Report) -> TrendPoint:
    return TrendPoint(
        report_id=report.id,
        created_at=report.created_at,
        sha=report.sha,
        coverage=coverage(report),
        **{m: rate(report, m) for m in METRICS},
    )


def project_out(project: Project, role: str | None, latest: ReportLite | None) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        owner=project.owner,
        repo=project.name,
        full_name=project.full_name,
        is_private=project.is_private,
        public_results=project.public_results,
        allow_text_upload=project.allow_text_upload,
        default_branch=project.default_branch,
        created_at=project.created_at,
        last_report_at=project.last_report_at,
        role=role,  # type: ignore[arg-type]
        latest=latest,
    )


def token_out(t: UploadToken) -> UploadTokenOut:
    return UploadTokenOut(
        id=t.id,
        name=t.name,
        prefix=t.prefix,
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        created_by=t.created_by_login,
    )


# ---- cursors ------------------------------------------------------------------------


def encode_cursor(value: str) -> str:
    raw = json.dumps({"k": value}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data: Any = json.loads(raw)
        value = data["k"]
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError
        return value
    except Exception:
        raise bad_request("Invalid cursor.") from None
