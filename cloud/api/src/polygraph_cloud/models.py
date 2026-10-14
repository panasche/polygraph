"""Database tables (SQLAlchemy 2.x). Works on PostgreSQL (production) and SQLite (dev/tests).

Personal data kept: GitHub user id, login and avatar URL (plus encrypted GitHub
tokens needed to read repo permissions). Nothing else about people. IP addresses
are only ever stored as keyed hashes inside rate-limit counters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JSONType = JSON().with_variant(JSONB(), "postgresql")
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {datetime: UTCDateTime(), dict[str, Any]: JSONType}


# --------------------------------------------------------------------------- #
# People and sign-in
# --------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(64))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    consented_privacy_version: Mapped[str | None] = mapped_column(String(32))
    consented_at: Mapped[datetime | None]
    last_synced_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class GithubCredential(Base):
    """GitHub user-to-server tokens, Fernet-encrypted. Used only to read repo permissions."""

    __tablename__ = "github_credentials"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    access_token_enc: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None]
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_expires_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class OAuthState(Base):
    """One-time OAuth state with its PKCE verifier. Lives 10 minutes."""

    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    next_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)


# --------------------------------------------------------------------------- #
# GitHub installations and projects
# --------------------------------------------------------------------------- #


class GithubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    account_login: Mapped[str] = mapped_column(String(64))
    account_type: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    github_repo_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    owner: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    full_name_lower: Mapped[str] = mapped_column(String(201), unique=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    public_results: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_text_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    installation_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_installations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_report_at: Mapped[datetime | None]

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class ProjectMember(Base):
    """Cached GitHub permission of a user on a repo. ``role`` is admin or member."""

    __tablename__ = "project_members"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    github_permission: Mapped[str | None] = mapped_column(String(16))
    synced_at: Mapped[datetime] = mapped_column(default=utcnow)


class UploadToken(Base):
    __tablename__ = "upload_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_login: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None]


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


class Report(Base):
    """One upload = one results file = one CI run of the test suite."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    branch: Mapped[str | None] = mapped_column(String(255))
    sha: Mapped[str | None] = mapped_column(String(40))
    ref: Mapped[str | None] = mapped_column(String(255))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    base_branch: Mapped[str | None] = mapped_column(String(255))
    event_name: Mapped[str | None] = mapped_column(String(64))

    framework: Mapped[str | None] = mapped_column(String(64))
    framework_version: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    config_label: Mapped[str | None] = mapped_column(String(64))
    polygraph_version: Mapped[str | None] = mapped_column(String(32))
    content_included: Mapped[bool] = mapped_column(Boolean, default=False)

    # Recomputed numbers (never the uploaded summary). Rates are rebuilt with Wilson CIs.
    runs: Mapped[int] = mapped_column(Integer, default=0)
    faulted_runs: Mapped[int] = mapped_column(Integer, default=0)
    success_num: Mapped[int] = mapped_column(Integer, default=0)
    success_den: Mapped[int] = mapped_column(Integer, default=0)
    silent_lie_num: Mapped[int] = mapped_column(Integer, default=0)
    silent_lie_den: Mapped[int] = mapped_column(Integer, default=0)
    double_action_num: Mapped[int] = mapped_column(Integer, default=0)
    double_action_den: Mapped[int] = mapped_column(Integer, default=0)
    hijack_num: Mapped[int] = mapped_column(Integer, default=0)
    hijack_den: Mapped[int] = mapped_column(Integer, default=0)
    coverage_applicable: Mapped[int] = mapped_column(Integer, default=0)
    coverage_covered: Mapped[int] = mapped_column(Integer, default=0)

    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    environment: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    coverage_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    tools: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # {"items": [...]}
    server_claims: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # {"items": [...]}

    raw_key: Mapped[str | None] = mapped_column(String(512))
    raw_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    runs_purged_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    processing_started_at: Mapped[datetime | None]
    processed_at: Mapped[datetime | None]

    __table_args__ = (
        Index("ix_reports_project_branch_created", "project_id", "branch", "created_at"),
        Index("ix_reports_project_status_created", "project_id", "status", "created_at"),
        Index("ix_reports_project_pr", "project_id", "pr_number"),
        Index("ix_reports_created_at", "created_at"),
    )


class ReportPair(Base):
    """One heatmap cell: (server, tool, fault) within a report."""

    __tablename__ = "report_pairs"

    report_id: Mapped[str] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True
    )
    server: Mapped[str] = mapped_column(String(128), primary_key=True)
    tool: Mapped[str] = mapped_column(String(128), primary_key=True)
    fault: Mapped[str] = mapped_column(String(32), primary_key=True)
    runs: Mapped[int] = mapped_column(Integer)
    bad: Mapped[int] = mapped_column(Integer)
    outcomes: Mapped[dict[str, Any]] = mapped_column(JSONType)


class RunIndex(Base):
    """Light per-run row for the report page. The full run lives in storage."""

    __tablename__ = "run_index"

    report_id: Mapped[str] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    test_id: Mapped[str] = mapped_column(String(1024))
    task: Mapped[str | None] = mapped_column(String(1024))
    fault: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    fault_name: Mapped[str | None] = mapped_column(String(32))
    repeat: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(16))
    is_bad: Mapped[bool] = mapped_column(Boolean, default=False)
    precedence: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[dict[str, Any]] = mapped_column(JSONType)  # {"items": [...]}
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_run_index_report_order", "report_id", "is_bad", "precedence", "ordinal"),
    )


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    request_id: Mapped[str | None] = mapped_column(String(64))


class RateLimitCounter(Base):
    """Fixed-window counters. ``key`` holds ids or keyed IP hashes, never raw IPs."""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
