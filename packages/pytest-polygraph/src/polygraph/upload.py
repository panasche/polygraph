"""Upload a results file to Polygraph Cloud (``polygraph upload``).

Auth is GitHub Actions OIDC when available, else a per-project ``POLYGRAPH_TOKEN``.
The file is prepared by :mod:`polygraph.privacy` first (payload text stripped unless
the project opted in with a mask), gzipped and POSTed to ``{api}/upload``.

No token, JWT or ``Authorization`` value is ever printed, logged or put in an
exception message. Server-provided text is passed through :func:`_redact` before
it is echoed.
"""

from __future__ import annotations

import gzip
import logging
import os
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from . import __version__
from .privacy import MaskError, prepare_for_upload
from .results import ResultsFile, parse

__all__ = [
    "DEFAULT_API",
    "DEFAULT_AUDIENCE",
    "UploadClient",
    "UploadError",
    "get_auth_header",
    "run_upload",
]

DEFAULT_API = "https://polygraphmcp.dev/api"
DEFAULT_AUDIENCE = "https://polygraphmcp.dev"
DEFAULT_RESULTS_PATH = Path(".polygraph") / "results.json"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 30.0
RETRY_AFTER_CAP_S = 60.0
JITTER_S = 0.1

_REPORT_ID = re.compile(r"^rep_[A-Za-z0-9_-]+$")
_REDACTED = "[REDACTED]"
# Generic secret shapes, redacted even when we do not know the exact value.
_SECRET_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),  # JWT
    re.compile(r"pgp_[A-Za-z0-9_-]+"),  # Polygraph upload token
    re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._~+/=-]{8,}"),  # auth header value
)

log = logging.getLogger("polygraph.upload")


class UploadError(RuntimeError):
    """Upload or API call failed. The message is safe to print (no secrets)."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    """Remove known secret values and anything shaped like a token from ``text``."""
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, _REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def _with_query_param(url: str, key: str, value: str) -> str:
    """Append ``key=value`` to ``url``'s query string, keeping any existing params."""
    parts = urlsplit(url)
    extra = urlencode({key: value})
    query = f"{parts.query}&{extra}" if parts.query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def get_auth_header(
    client: httpx.Client,
    env: Mapping[str, str] | None = None,
    *,
    audience: str | None = None,
) -> tuple[str, str]:
    """Return ``(Authorization header value, source)``; source is ``"oidc"`` or ``"token"``.

    GitHub Actions OIDC is used when ``ACTIONS_ID_TOKEN_REQUEST_URL`` and
    ``ACTIONS_ID_TOKEN_REQUEST_TOKEN`` are both set; otherwise ``POLYGRAPH_TOKEN``.
    The audience is ``audience``, else ``$POLYGRAPH_OIDC_AUDIENCE``, else
    :data:`DEFAULT_AUDIENCE`.
    """
    env = os.environ if env is None else env
    req_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    req_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if req_url and req_token:
        aud = audience or env.get("POLYGRAPH_OIDC_AUDIENCE") or DEFAULT_AUDIENCE
        try:
            resp = client.get(
                _with_query_param(req_url, "audience", aud),
                headers={"Authorization": f"bearer {req_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise UploadError(
                f"Could not reach the GitHub Actions OIDC endpoint ({type(exc).__name__})."
            ) from None
        if resp.status_code != 200:
            raise UploadError(
                f"GitHub Actions OIDC token request failed (HTTP {resp.status_code}). "
                "Check that the workflow has 'permissions: id-token: write'.",
                status_code=resp.status_code,
            )
        try:
            jwt = resp.json()["value"]
        except (ValueError, KeyError, TypeError):
            jwt = None
        if not isinstance(jwt, str) or not jwt:
            raise UploadError("GitHub Actions OIDC response had no token value.")
        log.debug("using GitHub Actions OIDC credentials (audience %s)", aud)
        return f"Bearer {jwt}", "oidc"
    token = env.get("POLYGRAPH_TOKEN")
    if token:
        log.debug("using POLYGRAPH_TOKEN credentials")
        return f"Token {token}", "token"
    raise UploadError(
        "No upload credentials. In GitHub Actions add 'permissions: id-token: write'; "
        "locally set POLYGRAPH_TOKEN."
    )


def _check_report_id(report_id: str) -> str:
    if not isinstance(report_id, str) or not _REPORT_ID.match(report_id):
        raise UploadError("Invalid report id (expected 'rep_...').")
    return report_id


class UploadClient:
    """Small client for the upload endpoints. Use as a context manager."""

    def __init__(
        self,
        api: str = DEFAULT_API,
        *,
        env: Mapping[str, str] | None = None,
        audience: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api = api.rstrip("/")
        self._env: Mapping[str, str] = os.environ if env is None else env
        self._audience = audience
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._auth: str | None = None
        self.auth_source: str | None = None
        self._secrets: list[str] = [
            v
            for k in ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "POLYGRAPH_TOKEN")
            if (v := self._env.get(k))
        ]

    # -- context manager ---------------------------------------------------- #

    def __enter__(self) -> UploadClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def redact(self, text: str) -> str:
        """Redact this client's credentials (and anything token-shaped) from ``text``."""
        return _redact(text, self._secrets)

    # -- internals ---------------------------------------------------------- #

    def _authorization(self) -> str:
        if self._auth is None:
            header, source = get_auth_header(self._client, self._env, audience=self._audience)
            self._auth, self.auth_source = header, source
            self._secrets += [header, header.split(" ", 1)[-1]]
        return self._auth

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization(),
            "User-Agent": f"pytest-polygraph/{__version__}",
            "Accept": "application/json",
        }

    def _backoff(self, attempt: int) -> float:
        delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2.0**attempt))
        return delay + random.uniform(0, JITTER_S)  # noqa: S311 - jitter, not crypto

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if value is None:
            return None
        try:
            seconds = float(value.strip())
        except ValueError:
            return None  # HTTP-date form: fall back to our own backoff
        return min(RETRY_AFTER_CAP_S, max(0.0, seconds))

    def _request(
        self,
        method: str,
        url: str,
        *,
        what: str,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send with retries on transient failures; return the last response."""
        all_headers = {**self._headers(), **(headers or {})}
        for attempt in range(self._max_attempts):
            last = attempt == self._max_attempts - 1
            try:
                resp = self._client.request(method, url, headers=all_headers, **kwargs)
            except httpx.TransportError as exc:
                if last:
                    raise UploadError(
                        f"{what} failed: could not reach {self.api} ({type(exc).__name__})."
                    ) from None
                delay = self._backoff(attempt)
                log.debug("%s: %s, retrying in %.2fs", what, type(exc).__name__, delay)
                self._sleep(delay)
                continue
            if resp.status_code in RETRY_STATUSES and not last:
                retry_after = self._retry_after(resp)
                delay = self._backoff(attempt) if retry_after is None else retry_after
                log.debug("%s: HTTP %s, retrying in %.2fs", what, resp.status_code, delay)
                self._sleep(delay)
                continue
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

    def _error(self, resp: httpx.Response, what: str) -> UploadError:
        code: str | None = None
        message = ""
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                code = str(err.get("code")) if err.get("code") is not None else None
                message = str(err.get("message") or "")
        except ValueError:
            pass
        code = _redact(code, self._secrets) if code else None
        message = _redact(message, self._secrets)[:500]
        text = f"{what} failed (HTTP {resp.status_code}"
        text += f" {code})" if code else ")"
        if message:
            text += f": {message}"
        return UploadError(text, status_code=resp.status_code, code=code)

    def _json(self, resp: httpx.Response, what: str) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise UploadError(f"{what}: unexpected response from the server.")
        return data

    # -- public API --------------------------------------------------------- #

    def upload(self, results: ResultsFile) -> dict[str, Any]:
        """POST the (already prepared) results; return ``{"report_id", "status", "url"}``."""
        body = gzip.compress(results.to_json().encode("utf-8"))
        resp = self._request(
            "POST",
            f"{self.api}/upload",
            what="Upload",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            content=body,
        )
        if not resp.is_success:
            raise self._error(resp, "Upload")
        data = self._json(resp, "Upload")
        _check_report_id(data.get("report_id", ""))
        log.debug("uploaded report %s (%s)", data["report_id"], data.get("status"))
        return data

    def status(self, report_id: str) -> dict[str, Any]:
        """``GET {api}/upload/{report_id}`` → ``{"report_id", "status", "rejection_reason"}``."""
        rid = _check_report_id(report_id)
        resp = self._request("GET", f"{self.api}/upload/{rid}", what="Status check")
        if not resp.is_success:
            raise self._error(resp, "Status check")
        return self._json(resp, "Status check")

    def wait(self, report_id: str, *, timeout_s: float = 300, poll_s: float = 2) -> dict[str, Any]:
        """Poll until the report is ``ready`` or ``rejected`` (returned, not raised)."""
        rid = _check_report_id(report_id)
        start = time.monotonic()
        slept = 0.0
        while True:
            st = self.status(rid)
            state = st.get("status")
            log.debug("report %s status %s", rid, state)
            if state in {"ready", "rejected"}:
                return st
            if max(slept, time.monotonic() - start) >= timeout_s:
                raise UploadError(
                    f"Timed out after {timeout_s:g}s waiting for report {rid} "
                    f"(last status: {_redact(str(state), self._secrets)})."
                )
            self._sleep(poll_s)
            slept += poll_s

    def compare(
        self,
        report_id: str,
        *,
        base: str = "branch:main",
        timeout_s: float = 120,
        poll_s: float = 2,
    ) -> dict[str, Any]:
        """``GET {api}/upload/{report_id}/compare?base=...``; keeps polling while 409."""
        rid = _check_report_id(report_id)
        start = time.monotonic()
        slept = 0.0
        while True:
            resp = self._request(
                "GET", f"{self.api}/upload/{rid}/compare", what="Compare", params={"base": base}
            )
            if resp.status_code != 409:
                if not resp.is_success:
                    raise self._error(resp, "Compare")
                return self._json(resp, "Compare")
            if max(slept, time.monotonic() - start) >= timeout_s:
                raise UploadError(
                    f"Timed out after {timeout_s:g}s waiting for the comparison of {rid}.",
                    status_code=409,
                    code="conflict",
                )
            log.debug("compare %s: still processing", rid)
            pause = self._retry_after(resp)
            pause = poll_s if pause is None else pause
            self._sleep(pause)
            slept += pause


def run_upload(
    path: str | os.PathLike[str] | None = None,
    *,
    api: str = DEFAULT_API,
    include_content: bool = False,
    mask: str | None = None,
    wait: bool = False,
    compare_to: str | None = None,
    pr_comment: str | os.PathLike[str] | None = None,
    audience: str | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    """Implementation of ``polygraph upload``. Returns the process exit code.

    0 = success; 1 = upload/API failure or the report was rejected;
    2 = bad input or a masking problem (nothing was sent).
    """
    file_path = Path(path) if path is not None else DEFAULT_RESULTS_PATH
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        out(f"polygraph upload: cannot read {file_path}: {exc.strerror or type(exc).__name__}")
        return 2
    try:
        results = parse(raw)
    except ValueError as exc:
        # Do not echo pydantic's message: it can quote payload text from the file.
        errors = getattr(exc, "error_count", lambda: None)()
        detail = f" ({errors} schema errors)" if errors else ""
        out(f"polygraph upload: {file_path} is not a valid polygraph results file{detail}.")
        return 2

    try:
        prepared = prepare_for_upload(results, include_content=include_content, mask=mask)
    except MaskError as exc:
        out(f"polygraph upload: {exc}. Nothing was uploaded.")
        return 2
    if not include_content:
        out("polygraph upload: payload text and free-text details stripped before upload.")

    try:
        with UploadClient(api, env=env, audience=audience, transport=transport, sleep=sleep) as c:
            info = c.upload(prepared)
            report_id = str(info["report_id"])
            url = str(info.get("url") or "")
            out(f"Polygraph report: {url or report_id}")

            if not (wait or compare_to or pr_comment):
                return 0
            st = c.wait(report_id)
            if st.get("status") == "rejected":
                reason = c.redact(str(st.get("rejection_reason") or "no reason given"))
                out(f"polygraph upload: report {report_id} was rejected: {reason}")
                return 1
            out(f"Report {report_id} is ready.")

            compare_json: dict[str, Any] | None = None
            if compare_to:
                try:
                    compare_json = c.compare(report_id, base=f"branch:{compare_to}")
                except UploadError as exc:
                    if exc.status_code != 404:
                        raise
                    out(f"No report on '{compare_to}' to compare with yet.")
    except UploadError as exc:
        out(f"polygraph upload: {exc}")
        return 1

    if pr_comment:
        # Render from the uploaded (prepared) copy so no stripped text reaches a PR.
        from .report.markdown import render_pr_comment  # lazy: keeps `import upload` light

        md = render_pr_comment(
            prepared,
            compare=compare_json,
            base_label=compare_to or "main",
            details_url=url or None,
        )
        Path(pr_comment).write_text(md, encoding="utf-8")
        out(f"Wrote PR comment to {pr_comment}")
    return 0
