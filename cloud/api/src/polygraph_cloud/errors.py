"""The error envelope: ``{"error": {"code": ..., "message": ...}}`` (CONTRACTS.md 4)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logs import get_logger

log = get_logger(__name__)

STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "bad_request",
    409: "conflict",
    413: "payload_too_large",
    415: "bad_request",
    422: "bad_request",
    429: "rate_limited",
    500: "internal",
    503: "internal",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)


def unauthorized(message: str = "Sign in first.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str = "You don't have permission to do that.") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def project_not_found() -> ApiError:
    # Same answer for "doesn't exist" and "you can't see it": never leak existence.
    return ApiError(404, "not_found", "Project not found.")


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


def too_large(message: str) -> ApiError:
    return ApiError(413, "payload_too_large", message)


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Too many requests. Try again later.",
        {"Retry-After": str(max(1, retry_after))},
    )


def envelope(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(envelope(code, message), status_code=status, headers=headers)


def _validation_message(exc: RequestValidationError) -> str:
    # Field locations and messages only. Never echo the submitted values.
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body",))
        parts.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return "Invalid request. " + "; ".join(parts)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(400, "bad_request", _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_CODES.get(exc.status_code, "bad_request")
        message = {404: "Not found.", 405: "Method not allowed."}.get(
            exc.status_code, "Request failed."
        )
        return error_response(exc.status_code, code, message, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled_error",
            error_type=type(exc).__name__,
            path=request.url.path,
            exc_info=exc,
        )
        return error_response(500, "internal", "Something went wrong on our side.")
