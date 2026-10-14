"""Object storage: S3 (SSE-KMS) in the cloud, a local folder for development.

Keys (CONTRACTS.md 5):
- ``raw/{project_id}/{report_id}.json.gz``
- ``runs/{project_id}/{report_id}/{run_id}.json``
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

import anyio
import anyio.to_thread

from .config import Settings

_KEY = re.compile(r"^[A-Za-z0-9_\-./]+$")


def raw_key(project_id: str, report_id: str) -> str:
    return f"raw/{project_id}/{report_id}.json.gz"


def runs_prefix(project_id: str, report_id: str) -> str:
    return f"runs/{project_id}/{report_id}/"


def run_key(project_id: str, report_id: str, run_id: str) -> str:
    return f"{runs_prefix(project_id, report_id)}{run_id}.json"


def project_prefixes(project_id: str) -> list[str]:
    return [f"raw/{project_id}/", f"runs/{project_id}/"]


def _check_key(key: str) -> str:
    if not _KEY.match(key) or ".." in key or key.startswith("/"):
        raise ValueError("invalid storage key")
    return key


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = ...) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def list_keys(self, prefix: str) -> list[str]: ...
    async def ping(self) -> None: ...


class LocalStorage:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        objects = Path(root) / "objects"
        objects.mkdir(parents=True, exist_ok=True)
        self.root = objects.resolve()

    def _path(self, key: str) -> Path:
        # _check_key allows only [A-Za-z0-9_-./], no "..", no leading "/": joining is safe.
        return self.root / _check_key(key)

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    async def put(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        await anyio.to_thread.run_sync(self._put, key, data)

    def _get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    async def get(self, key: str) -> bytes | None:
        return await anyio.to_thread.run_sync(self._get, key)

    def _delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def delete(self, key: str) -> None:
        await anyio.to_thread.run_sync(self._delete, key)

    def _list(self, prefix: str) -> list[str]:
        _check_key(prefix)
        dir_part = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        base = self._path(dir_part) if dir_part else self.root
        if not base.is_dir():
            return []
        out = []
        for p in base.rglob("*"):
            if p.is_file() and not p.name.startswith(".tmp-"):
                rel = p.relative_to(self.root).as_posix()
                if rel.startswith(prefix):
                    out.append(rel)
        return sorted(out)

    async def list_keys(self, prefix: str) -> list[str]:
        return await anyio.to_thread.run_sync(self._list, prefix)

    def _delete_prefix(self, prefix: str) -> int:
        keys = self._list(prefix)
        for k in keys:
            self._path(k).unlink(missing_ok=True)
        # Tidy empty folders under the prefix.
        base = self._path(prefix.rstrip("/")) if prefix.rstrip("/") else self.root
        if base.is_dir():
            for d in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
                with contextlib.suppress(OSError):
                    d.rmdir()
            with contextlib.suppress(OSError):
                base.rmdir()
        return len(keys)

    async def delete_prefix(self, prefix: str) -> int:
        return await anyio.to_thread.run_sync(self._delete_prefix, prefix)

    async def ping(self) -> None:
        def _ping() -> None:
            probe = self.root / ".ready-probe"
            probe.write_bytes(b"ok")
            probe.unlink()

        await anyio.to_thread.run_sync(_ping)


class S3Storage:
    """S3 with SSE-KMS. boto3 is synchronous, so calls run in worker threads."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required for STORAGE_BACKEND=s3")
        self.bucket = settings.s3_bucket
        self.kms_key_id = settings.kms_key_id
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                region_name=settings.aws_region,
                endpoint_url=settings.s3_endpoint_url or None,
                config=Config(retries={"max_attempts": 5, "mode": "standard"}),
            )
        self.client = client
        self._limiter = anyio.CapacityLimiter(16)

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs), limiter=self._limiter)

    async def put(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        extra: dict[str, Any] = {}
        if self.kms_key_id:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
        await self._run(
            self.client.put_object,
            Bucket=self.bucket,
            Key=_check_key(key),
            Body=data,
            ContentType=content_type,
            **extra,
        )

    async def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            obj = await self._run(self.client.get_object, Bucket=self.bucket, Key=_check_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return await anyio.to_thread.run_sync(obj["Body"].read, limiter=self._limiter)

    async def delete(self, key: str) -> None:
        await self._run(self.client.delete_object, Bucket=self.bucket, Key=_check_key(key))

    async def list_keys(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            keys: list[str] = []
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=_check_key(prefix)):
                keys.extend(o["Key"] for o in page.get("Contents", []))
            return keys

        return await anyio.to_thread.run_sync(_list, limiter=self._limiter)

    async def delete_prefix(self, prefix: str) -> int:
        keys = await self.list_keys(prefix)
        for i in range(0, len(keys), 1000):
            batch = [{"Key": k} for k in keys[i : i + 1000]]
            await self._run(
                self.client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": batch, "Quiet": True},
            )
        return len(keys)

    async def ping(self) -> None:
        await self._run(self.client.head_bucket, Bucket=self.bucket)


def make_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.local_storage_dir)
