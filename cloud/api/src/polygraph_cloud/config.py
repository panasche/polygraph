"""Settings, read from the environment (CONTRACTS.md section 6) and checked at startup.

Outside ``ENVIRONMENT=development`` the service refuses to start with anything
that only makes sense on a laptop: SQLite, local storage, the inline queue,
missing or weak secrets, or a plain-HTTP public URL.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Marker inside every generated development secret. Seeing it outside
#: development means somebody copied a dev config into a real deployment.
DEV_SECRET_MARKER = "dev-insecure"  # noqa: S105 (a marker, not a secret)

_DEV_SESSION_SECRET = f"{DEV_SECRET_MARKER}-session-secret-change-me-0123456789"


def _dev_fernet_key() -> str:
    raw = hashlib.sha256(f"{DEV_SECRET_MARKER}-token-key".encode()).digest()
    return base64.urlsafe_b64encode(raw).decode()


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe or incomplete for the environment."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # ---- CONTRACTS.md section 6 ----------------------------------------------------
    environment: Literal["development", "staging", "production"] = "development"
    public_base_url: str = "http://localhost:3000"
    database_url: str = "sqlite:///./.data/dev.db"

    storage_backend: Literal["s3", "local"] = "local"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    local_storage_dir: str = "./.data"
    kms_key_id: str | None = None

    queue_backend: Literal["sqs", "inline"] = "inline"
    sqs_queue_url: str | None = None
    sqs_endpoint_url: str | None = None

    github_app_id: str | None = None
    github_app_client_id: str | None = None
    github_app_client_secret: SecretStr | None = None
    github_app_private_key: SecretStr | None = None

    session_secret: SecretStr | None = None
    token_encryption_key: SecretStr | None = None

    oidc_audience: str = "https://polygraphmcp.dev"
    leaderboard_project: str = "polygraph-dev/benchmark"
    privacy_version: str = "2026-10-13"
    pii_scanner: Literal["presidio", "regex"] = "regex"

    # ---- Optional knobs (documented in README; safe defaults) ----------------------
    aws_region: str = "ap-south-1"
    auto_migrate: bool = True  # development + SQLite only: apply migrations at startup
    log_level: str = "INFO"
    oidc_issuer: str = "https://token.actions.githubusercontent.com"
    oidc_jwks_url: str = "https://token.actions.githubusercontent.com/.well-known/jwks"
    oidc_leeway_s: int = Field(default=60, ge=0, le=300)
    jwks_cache_ttl_s: int = Field(default=3600, ge=60)
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    max_upload_bytes: int = 10 * 1024 * 1024
    max_uncompressed_bytes: int = 50 * 1024 * 1024
    max_request_bytes: int = 1024 * 1024  # every other endpoint
    max_runs_per_file: int = Field(default=5_000, ge=1, le=5_000)
    uploads_per_project_per_hour: int = Field(default=60, ge=1)
    uploads_per_ip_per_hour: int = Field(default=600, ge=1)
    max_tokens_per_project: int = Field(default=20, ge=1)
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    session_days: int = Field(default=30, ge=1, le=90)
    membership_max_age_hours: int = Field(default=24, ge=1)
    auto_sync_after_minutes: int = Field(default=60, ge=1)

    run_retention_days: int = Field(default=90, ge=1)
    report_retention_days: int = Field(default=730, ge=1)

    sqs_visibility_timeout_s: int = Field(default=300, ge=30)
    sqs_wait_time_s: int = Field(default=20, ge=0, le=20)
    worker_max_attempts: int = Field(default=5, ge=1)

    # ---------------------------------------------------------------------------------

    @field_validator("public_base_url", "github_api_url", "github_web_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("github_app_private_key")
    @classmethod
    def _unescape_pem(cls, v: SecretStr | None) -> SecretStr | None:
        # .env files carry the PEM on one line with literal \n escapes.
        if v is None or "\\n" not in v.get_secret_value():
            return v
        return SecretStr(v.get_secret_value().replace("\\n", "\n"))

    @model_validator(mode="after")
    def _fill_dev_defaults(self) -> Settings:
        if self.environment == "development":
            if self.session_secret is None:
                self.session_secret = SecretStr(_DEV_SESSION_SECRET)
            if self.token_encryption_key is None:
                self.token_encryption_key = SecretStr(_dev_fernet_key())
        return self

    # ---- derived -------------------------------------------------------------------

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def allowed_origins(self) -> list[str]:
        origins = [self.public_base_url]
        if self.is_dev:
            for o in ("http://localhost:3000", "http://127.0.0.1:3000"):
                if o not in origins:
                    origins.append(o)
        return origins

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite+aiosqlite:"):
            return url
        if url.startswith("sqlite:"):
            return "sqlite+aiosqlite:" + url[len("sqlite:") :]
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("postgresql+asyncpg://"):
            return url
        return url

    @property
    def sync_database_url(self) -> str:
        url = self.async_database_url
        if url.startswith("sqlite+aiosqlite:"):
            return "sqlite:" + url[len("sqlite+aiosqlite:") :]
        if url.startswith("postgresql+asyncpg://"):
            return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
        return url

    def secret(self, name: str) -> str:
        value = getattr(self, name)
        if value is None:
            raise ConfigError(f"{name.upper()} is not set")
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_app_client_id and self.github_app_client_secret)

    # ---- startup checks ------------------------------------------------------------

    def problems(self) -> list[str]:
        """Return every reason this config is unsafe for its environment."""
        if self.is_dev:
            return []
        out: list[str] = []
        session = self.session_secret.get_secret_value() if self.session_secret else ""
        if len(session.encode()) < 32:
            out.append("SESSION_SECRET must be at least 32 bytes")
        if DEV_SECRET_MARKER in session:
            out.append("SESSION_SECRET is a development default")
        fernet = self.token_encryption_key.get_secret_value() if self.token_encryption_key else ""
        if not fernet:
            out.append("TOKEN_ENCRYPTION_KEY is required")
        else:
            try:
                from cryptography.fernet import Fernet

                Fernet(fernet.encode())
            except Exception:
                out.append("TOKEN_ENCRYPTION_KEY is not a valid Fernet key")
            if fernet == _dev_fernet_key():
                out.append("TOKEN_ENCRYPTION_KEY is a development default")
        if self.is_sqlite:
            out.append("DATABASE_URL must be PostgreSQL outside development")
        if not self.public_base_url.startswith("https://"):
            out.append("PUBLIC_BASE_URL must use https outside development")
        if self.storage_backend != "s3" or not self.s3_bucket:
            out.append("STORAGE_BACKEND=s3 and S3_BUCKET are required outside development")
        if self.queue_backend != "sqs" or not self.sqs_queue_url:
            out.append("QUEUE_BACKEND=sqs and SQS_QUEUE_URL are required outside development")
        if self.is_production and not self.kms_key_id:
            out.append("KMS_KEY_ID is required in production")
        for name in (
            "github_app_id",
            "github_app_client_id",
            "github_app_client_secret",
            "github_app_private_key",
        ):
            if not getattr(self, name):
                out.append(f"{name.upper()} is required outside development")
        if self.pii_scanner == "presidio":
            from .pii import presidio_available

            if not presidio_available():
                out.append("PII_SCANNER=presidio but presidio-analyzer is not installed")
        return out

    def check(self) -> None:
        problems = self.problems()
        if problems:
            raise ConfigError(f"Refusing to start in {self.environment}: " + "; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
