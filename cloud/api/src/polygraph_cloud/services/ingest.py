"""Accepting an upload: decompress safely, validate, store the raw file, create the report row.

Used by ``POST /api/upload`` and by ``polygraph_cloud.cli seed`` so local demos
go through exactly the same path.
"""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass

import anyio.to_thread
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from polygraph import results as pr

from ..context import AppContext
from ..errors import ApiError, bad_request, too_large
from ..ids import new_id
from ..models import Project, Report
from ..oidc import ActionsIdentity
from ..storage import raw_key

GZIP_MAGIC = b"\x1f\x8b"
_CHUNK = 64 * 1024


class TooLarge(Exception):
    pass


def gunzip_capped(data: bytes, max_bytes: int) -> bytes:
    """Decompress gzip without ever holding more than ``max_bytes`` of output.

    Streams through ``zlib`` with ``max_length`` so a gzip bomb stops at the cap
    instead of filling memory. Handles multi-member files.
    """
    out = bytearray()
    buf = data
    while True:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        while not d.eof:
            room = max_bytes - len(out)
            chunk = d.decompress(buf, max(1, min(room + 1, _CHUNK * 16)))
            out += chunk
            if len(out) > max_bytes:
                raise TooLarge
            buf = d.unconsumed_tail
            if not chunk and not buf:
                break  # needs more input than we have
        if not d.eof:
            raise ValueError("truncated gzip data")
        buf = d.unused_data
        if not buf.startswith(GZIP_MAGIC):
            break  # end of data (or trailing garbage, ignored like gzip(1))
    return bytes(out)


def is_gzip(data: bytes, content_encoding: str | None) -> bool:
    if content_encoding and "gzip" in content_encoding.lower():
        return True
    return data[:2] == GZIP_MAGIC


def decode_body(data: bytes, content_encoding: str | None, max_uncompressed: int) -> bytes:
    if is_gzip(data, content_encoding):
        try:
            return gunzip_capped(data, max_uncompressed)
        except TooLarge:
            raise too_large(
                f"The results file is larger than {max_uncompressed // (1024 * 1024)} MB "
                "uncompressed."
            ) from None
        except (zlib.error, ValueError, EOFError):
            raise bad_request("The body is not valid gzip.") from None
    if content_encoding and content_encoding.lower() not in ("identity", ""):
        raise ApiError(415, "bad_request", "Only gzip content encoding is supported.")
    if len(data) > max_uncompressed:
        raise too_large("The results file is too large.")
    return data


def _validation_summary(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc or '(root)'}: {err.get('msg', 'invalid')}")
    more = exc.error_count() - len(parts)
    suffix = f" (+{more} more)" if more > 0 else ""
    return "; ".join(parts) + suffix


def parse_results(data: bytes, max_runs: int) -> pr.ResultsFile:
    """Validate with the frozen ``polygraph.results`` models. Raises ApiError."""
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise bad_request("The body is not valid JSON.") from None
    if not isinstance(doc, dict):
        raise bad_request("The results file must be a JSON object.")
    schema = doc.get("schema")
    if schema != pr.SCHEMA_ID:
        raise ApiError(
            422,
            "unsupported_schema",
            f"Unsupported results schema. This server reads {pr.SCHEMA_ID}.",
        )
    runs = doc.get("runs")
    if isinstance(runs, list) and len(runs) > max_runs:
        raise too_large(f"The results file has more than {max_runs} runs.")
    try:
        return pr.parse(doc)
    except ValidationError as exc:
        raise ApiError(
            422, "bad_request", "The results file failed validation: " + _validation_summary(exc)
        ) from None


async def parse_results_async(data: bytes, max_runs: int) -> pr.ResultsFile:
    return await anyio.to_thread.run_sync(parse_results, data, max_runs)


@dataclass
class GitMeta:
    branch: str | None
    sha: str | None
    ref: str | None
    pr_number: int | None
    base_branch: str | None
    event_name: str | None


def _clip(value: str | None, n: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:n] or None


UNSAFE_OIDC_EVENTS = frozenset({"workflow_run", "pull_request_target"})


def git_meta(results: pr.ResultsFile, identity: ActionsIdentity | None) -> GitMeta:
    """OIDC claims override whatever the file says (CONTRACTS.md 4, Upload)."""
    if identity is not None:
        if identity.event_name in UNSAFE_OIDC_EVENTS:
            # These events run with the base repo's token, so `ref` names the default
            # branch even when the code came from a fork. Accepting them would let a
            # fork's results land on main.
            raise ApiError(
                422,
                "rejected",
                f"Uploads from `{identity.event_name}` workflows aren't accepted, because their "
                "token names the base branch rather than the code that ran. Comment on the PR "
                "from that job instead; see the polygraph-action README for the fork pattern.",
            )
        return GitMeta(
            branch=_clip(identity.branch, 255),
            sha=_clip(identity.sha.lower(), 40),
            ref=_clip(identity.ref, 255),
            pr_number=identity.pr_number,
            base_branch=_clip(identity.base_ref, 255),
            event_name=_clip(identity.event_name, 64),
        )
    g = results.environment.git
    branch = g.branch
    if branch is None and g.ref and g.ref.startswith("refs/heads/"):
        branch = g.ref[len("refs/heads/") :]
    return GitMeta(
        branch=_clip(branch, 255),
        sha=_clip(g.sha.lower() if g.sha else None, 40),
        ref=_clip(g.ref, 255),
        pr_number=g.pr_number,
        base_branch=_clip(g.base_branch, 255),
        event_name=None,
    )


def check_content_policy(results: pr.ResultsFile, project: Project) -> None:
    if results.content_included and not project.allow_text_upload:
        raise ApiError(
            422,
            "rejected",
            "This upload includes payload text, but the project hasn't turned on text upload. "
            "Upload without --include-content, or ask a project admin to allow text.",
        )


async def create_report(
    ctx: AppContext,
    db: AsyncSession,
    project: Project,
    results: pr.ResultsFile,
    decoded: bytes,
    *,
    source: str,
    identity: ActionsIdentity | None = None,
    original_gz: bytes | None = None,
) -> Report:
    """Store the raw file and create a ``queued`` report row (caller commits and enqueues)."""
    meta = git_meta(results, identity)
    env = results.environment
    report_id = new_id("rep")
    key = raw_key(project.id, report_id)
    blob = original_gz if original_gz is not None else gzip.compress(decoded, compresslevel=6)
    await ctx.storage.put(key, blob, content_type="application/gzip")
    report = Report(
        id=report_id,
        project_id=project.id,
        status="queued",
        source=source,
        branch=meta.branch,
        sha=meta.sha,
        ref=meta.ref,
        pr_number=meta.pr_number,
        base_branch=meta.base_branch,
        event_name=meta.event_name,
        framework=_clip(env.framework, 64),
        framework_version=_clip(env.framework_version, 64),
        model=_clip(env.model, 128),
        config_label=_clip(env.config_label, 64),
        polygraph_version=_clip(env.polygraph_version, 32),
        content_included=results.content_included,
        raw_key=key,
        raw_bytes=len(blob),
    )
    db.add(report)
    return report
