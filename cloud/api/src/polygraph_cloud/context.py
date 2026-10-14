"""Everything the API and the worker share, built once per process."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .db import make_engine, make_sessionmaker
from .github import GitHubClient
from .oidc import OIDCVerifier
from .pii import Scanner, get_scanner
from .queue import Queue, make_queue
from .security import Encryptor, Signer
from .storage import Storage, make_storage


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: Storage
    queue: Queue
    http: httpx.AsyncClient
    oidc: OIDCVerifier
    github: GitHubClient
    encryptor: Encryptor
    state_signer: Signer
    scanner: Scanner
    extras: dict[str, object] = field(default_factory=dict)

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


def build_context(
    settings: Settings,
    *,
    storage: Storage | None = None,
    queue: Queue | None = None,
    http: httpx.AsyncClient | None = None,
) -> AppContext:
    engine = make_engine(settings)
    client = http or httpx.AsyncClient(
        headers={"User-Agent": "polygraph-cloud"},
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
    )
    return AppContext(
        settings=settings,
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        storage=storage or make_storage(settings),
        queue=queue or make_queue(settings),
        http=client,
        oidc=OIDCVerifier(settings, client),
        github=GitHubClient(settings, client),
        encryptor=Encryptor(settings.secret("token_encryption_key")),
        state_signer=Signer(settings.secret("session_secret"), "oauth-state"),
        scanner=get_scanner(settings.pii_scanner),
    )
