"""GitHub Actions OIDC token verification.

The JWKS is cached for ``JWKS_CACHE_TTL_S``. A token signed with a key id we
haven't seen triggers one refetch (GitHub rotates keys), throttled so a flood
of junk tokens can't hammer GitHub.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .config import Settings
from .logs import get_logger

log = get_logger(__name__)

REQUIRED_CLAIMS = ["iss", "aud", "exp", "iat", "repository", "repository_id", "ref", "sha"]


class OIDCError(Exception):
    """Verification failed. The message is safe to show (no token contents)."""


@dataclass
class ActionsIdentity:
    repository: str
    repository_id: int
    repository_owner: str
    visibility: str  # public | private | internal
    ref: str
    sha: str
    event_name: str | None
    head_ref: str | None
    base_ref: str | None

    @property
    def branch(self) -> str | None:
        if self.ref.startswith("refs/heads/"):
            return self.ref[len("refs/heads/") :]
        if self.ref.startswith("refs/pull/"):
            return self.head_ref or None
        if self.ref.startswith("refs/tags/"):
            return None
        return None

    @property
    def pr_number(self) -> int | None:
        parts = self.ref.split("/")
        if len(parts) >= 4 and parts[0] == "refs" and parts[1] == "pull" and parts[2].isdigit():
            return int(parts[2])
        return None

    @property
    def is_public(self) -> bool:
        return self.visibility == "public"


class JWKSCache:
    def __init__(
        self, url: str, client: httpx.AsyncClient, ttl_s: int = 3600, min_refresh_s: int = 30
    ) -> None:
        self.url = url
        self.client = client
        self.ttl_s = ttl_s
        self.min_refresh_s = min_refresh_s
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = anyio.Lock()

    async def _refresh(self) -> None:
        resp = await self.client.get(self.url, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        keys = {}
        for jwk in data.get("keys", []):
            if jwk.get("kty") != "RSA" or "kid" not in jwk:
                continue
            keys[jwk["kid"]] = RSAAlgorithm.from_jwk(json.dumps(jwk))
        if not keys:
            raise OIDCError("The OIDC key set is empty.")
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def get(self, kid: str) -> Any:
        now = time.monotonic()
        if kid in self._keys and now - self._fetched_at < self.ttl_s:
            return self._keys[kid]
        async with self._lock:
            now = time.monotonic()
            stale = now - self._fetched_at >= self.ttl_s
            unknown = kid not in self._keys
            if stale or (unknown and now - self._fetched_at >= self.min_refresh_s):
                try:
                    await self._refresh()
                except OIDCError:
                    raise
                except Exception as exc:
                    log.warning("jwks_fetch_failed", error_type=type(exc).__name__)
                    if not self._keys:
                        raise OIDCError("Could not fetch the OIDC signing keys.") from exc
        key = self._keys.get(kid)
        if key is None:
            raise OIDCError("The token was signed with an unknown key.")
        return key


class OIDCVerifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.issuer = settings.oidc_issuer
        self.audience = settings.oidc_audience
        self.leeway = settings.oidc_leeway_s
        self.jwks = JWKSCache(settings.oidc_jwks_url, client, ttl_s=settings.jwks_cache_ttl_s)

    async def verify(self, token: str) -> ActionsIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCError("The OIDC token is malformed.") from exc
        if header.get("alg") != "RS256":
            raise OIDCError("The OIDC token uses an unsupported algorithm.")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise OIDCError("The OIDC token has no key id.")
        key = await self.jwks.get(kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": REQUIRED_CLAIMS, "verify_iat": True, "verify_nbf": True},
            )
        except jwt.ExpiredSignatureError as exc:
            raise OIDCError("The OIDC token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            raise OIDCError("The OIDC token audience is wrong.") from exc
        except jwt.InvalidIssuerError as exc:
            raise OIDCError("The OIDC token issuer is wrong.") from exc
        except jwt.ImmatureSignatureError as exc:
            raise OIDCError("The OIDC token is not valid yet.") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise OIDCError("The OIDC token is missing a required claim.") from exc
        except jwt.PyJWTError as exc:
            raise OIDCError("The OIDC token is invalid.") from exc

        iat = claims.get("iat")
        if isinstance(iat, (int, float)) and iat > time.time() + self.leeway:
            raise OIDCError("The OIDC token was issued in the future.")
        repo = claims.get("repository")
        if not isinstance(repo, str) or repo.count("/") != 1:
            raise OIDCError("The OIDC token has an invalid repository claim.")
        try:
            repo_id = int(claims["repository_id"])
        except (TypeError, ValueError) as exc:
            raise OIDCError("The OIDC token has an invalid repository_id claim.") from exc
        sha = str(claims.get("sha", ""))
        ref = str(claims.get("ref", ""))
        return ActionsIdentity(
            repository=repo,
            repository_id=repo_id,
            repository_owner=str(claims.get("repository_owner") or repo.split("/")[0]),
            visibility=str(claims.get("repository_visibility") or "private"),
            ref=ref,
            sha=sha,
            event_name=claims.get("event_name"),
            head_ref=claims.get("head_ref") or None,
            base_ref=claims.get("base_ref") or None,
        )
