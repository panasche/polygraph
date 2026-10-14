"""Audit trail of security-relevant actions. Ids only, never payload text or secrets."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog


def record(
    db: AsyncSession,
    action: str,
    *,
    actor_user_id: str | None = None,
    project_id: str | None = None,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    db.add(
        AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            project_id=project_id,
            target=target,
            detail=detail,
            request_id=request_id,
        )
    )
