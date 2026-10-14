"""FastAPI application factory. Run with ``uvicorn polygraph_cloud.app:create_app --factory``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import auth, me, projects, public, upload
from .api.middleware import ApiMiddleware
from .config import Settings, get_settings
from .context import AppContext, build_context
from .errors import install_error_handlers
from .logs import configure_logging, get_logger

log = get_logger(__name__)


def create_app(settings: Settings | None = None, context: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    settings.check()  # refuse unsafe production/staging configs before anything else
    ctx = context or build_context(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.is_dev and settings.is_sqlite and settings.auto_migrate:
            # Dev convenience: same Alembic migrations production runs as a release step.
            import anyio.to_thread

            from .cli import migrate

            await anyio.to_thread.run_sync(migrate, settings)
        log.info("api_started", environment=settings.environment, version=__version__)
        try:
            yield
        finally:
            await ctx.aclose()

    docs = not settings.is_production
    app = FastAPI(
        title="Polygraph Cloud API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.ctx = ctx
    install_error_handlers(app)

    app.include_router(auth.router)
    if settings.is_dev:
        app.include_router(auth.dev_router)
    app.include_router(me.router)
    app.include_router(projects.router)
    app.include_router(upload.router)
    app.include_router(public.router)

    # Added first = innermost. CORS sits outside so even rejections carry CORS headers.
    app.add_middleware(ApiMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Token",
            "Authorization",
            "Content-Encoding",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    return app
