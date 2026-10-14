"""Structured JSON logs with redaction.

Logs carry ids, never payload text, tokens, cookies or JWTs (CONTRACTS.md 7).
Two layers: keys that look sensitive are dropped, and string values that look
like credentials are masked wherever they appear.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|cookie|authorization|jwt|credential|private_key|"
    r"code_verifier|^code$|^state$|^text$|payload|session)",
    re.IGNORECASE,
)
_SAFE_KEYS = {"session_id", "token_id", "payload_bytes", "report_id", "status_code"}

_SECRET_VALUE = re.compile(
    r"(eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"  # JWT
    r"|pgp_[A-Za-z0-9]{8,}"  # upload tokens
    r"|gh[pousr]_[A-Za-z0-9]{16,}"  # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def scrub(value: str) -> str:
    return _SECRET_VALUE.sub("[redacted]", value)


def _redact(obj: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k not in _SAFE_KEYS and _SENSITIVE_KEY.search(k):
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return scrub(obj)
    return obj


def redact_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return _redact(event_dict)


_configured = False


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    global _configured
    lvl = getattr(logging, level.upper(), logging.INFO)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redact_processor,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    if not _configured:
        # Keep library loggers quiet and free of request lines with query strings.
        logging.basicConfig(level=max(lvl, logging.WARNING), stream=sys.stderr)
        for noisy in ("uvicorn.access", "botocore", "boto3", "urllib3", "httpx", "aiosqlite"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").disabled = True
        _configured = True


def get_logger(name: str = "polygraph_cloud") -> Any:
    return structlog.get_logger(name)
