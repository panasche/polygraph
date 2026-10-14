"""Response and request shapes (CONTRACTS.md section 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from polygraph.results import PassK, Rate

T = TypeVar("T")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(Out, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class Items(Out, Generic[T]):
    items: list[T]


class UserOut(Out):
    id: str
    login: str
    avatar_url: str | None
    consented_privacy_version: str | None
    created_at: datetime


class CoverageCount(Out):
    applicable: int
    covered: int


class ReportLite(Out):
    id: str
    project: str
    status: Literal["queued", "processing", "ready", "rejected"]
    rejection_reason: str | None
    source: Literal["oidc", "token"]
    branch: str | None
    sha: str | None
    pr_number: int | None
    created_at: datetime
    framework: str | None
    model: str | None
    config_label: str | None
    runs: int
    faulted_runs: int
    success: Rate
    silent_lie: Rate
    double_action: Rate
    hijack: Rate
    coverage: CoverageCount


class HeatCell(Out):
    server: str
    tool: str
    fault: str
    runs: int
    outcomes: dict[str, int]
    bad: int


class RunRow(Out):
    id: str
    test_id: str
    task: str | None
    fault: dict[str, Any] | None
    repeat: int
    outcome: str
    reasons: list[str]
    duration_ms: float


class ReportDetail(ReportLite):
    environment: dict[str, Any] | None
    summary: dict[str, Any] | None
    coverage_detail: dict[str, Any] | None
    tools: list[dict[str, Any]]
    server_claims: list[dict[str, Any]]
    content_included: bool
    heatmap: list[HeatCell]
    run_list: list[RunRow]


class ProjectOut(Out):
    id: str
    owner: str
    repo: str
    full_name: str
    is_private: bool
    public_results: bool
    allow_text_upload: bool
    default_branch: str
    created_at: datetime
    last_report_at: datetime | None
    role: Literal["admin", "member", "viewer"] | None
    latest: ReportLite | None


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_results: bool | None = None
    allow_text_upload: bool | None = None


class BranchOut(Out):
    name: str
    last_report_at: datetime | None
    reports: int


class TrendPoint(Out):
    report_id: str
    created_at: datetime
    sha: str | None
    success: Rate
    silent_lie: Rate
    double_action: Rate
    hijack: Rate
    coverage: CoverageCount


class Trends(Out):
    branch: str
    points: list[TrendPoint]


class MetricPair(Out):
    base: Rate
    head: Rate


class CoveragePairOut(Out):
    base: CoverageCount
    head: CoverageCount


class PairSide(Out):
    runs: int
    bad: int


class ComparePair(Out):
    server: str
    tool: str
    fault: str
    base: PairSide | None
    head: PairSide | None


class Compare(Out):
    base: ReportLite
    head: ReportLite
    metrics: dict[str, MetricPair]
    coverage: CoveragePairOut
    pairs: list[ComparePair]


class UploadTokenOut(Out):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    created_by: str | None


class UploadTokenCreated(UploadTokenOut):
    token: str


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 ._\-]+$")


class UploadAccepted(Out):
    report_id: str
    status: str
    url: str


class UploadStatus(Out):
    report_id: str
    status: str
    rejection_reason: str | None


class LeaderboardEntry(Out):
    framework: str | None
    framework_version: str | None
    config_label: str | None
    model: str | None
    report_id: str
    runs: int
    success: Rate
    silent_lie: Rate
    double_action: Rate
    hijack: Rate
    pass_k: PassK | None


class Leaderboard(Out):
    project: str
    updated_at: datetime | None
    entries: list[LeaderboardEntry]


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    privacy_version: str = Field(min_length=1, max_length=32)


class DevLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(default="dev-user", pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
