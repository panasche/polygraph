"""GitHub App calls: user authorization, token refresh, installations, repo permissions.

The App asks only for read-only *metadata* permission and no email scope. User
tokens are used for one thing: learning which installed repos a user can see
and with what permission.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from .config import Settings

MAX_REPOS = 3000


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class UserTokens:
    access_token: str
    access_expires_at: datetime | None
    refresh_token: str | None
    refresh_expires_at: datetime | None


@dataclass
class GitHubUser:
    id: int
    login: str
    avatar_url: str | None


@dataclass
class RepoAccess:
    repo_id: int
    owner: str
    name: str
    private: bool
    default_branch: str
    permission: str  # admin | maintain | push | triage | pull
    installation_id: int

    @property
    def role(self) -> str:
        return "admin" if self.permission in {"admin", "maintain"} else "member"


@dataclass
class Installation:
    id: int
    account_login: str
    account_type: str | None


def _permission(perms: dict[str, Any] | None) -> str:
    perms = perms or {}
    for name in ("admin", "maintain", "push", "triage", "pull"):
        if perms.get(name):
            return name
    return "pull"


def _expiry(seconds: Any) -> datetime | None:
    try:
        return datetime.now(UTC) + timedelta(seconds=int(seconds))
    except (TypeError, ValueError):
        return None


class GitHubClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = client
        self.api = settings.github_api_url
        self.web = settings.github_web_url

    @property
    def redirect_uri(self) -> str:
        return f"{self.s.public_base_url}/api/auth/github/callback"

    def authorize_url(self, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.s.github_app_client_id or "",
                "redirect_uri": self.redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.web}/login/oauth/authorize?{query}"

    def _headers(self, token: str | None = None, bearer: bool = False) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            h["Authorization"] = f"Bearer {token}" if bearer else f"token {token}"
        return h

    async def _token_request(self, data: dict[str, str]) -> UserTokens:
        payload = {
            "client_id": self.s.github_app_client_id or "",
            "client_secret": self.s.secret("github_app_client_secret"),
            **data,
        }
        resp = await self.http.post(
            f"{self.web}/login/oauth/access_token",
            data=payload,
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise GitHubError("GitHub token exchange failed.", resp.status_code)
        body = resp.json()
        if "error" in body or "access_token" not in body:
            raise GitHubError("GitHub rejected the authorization.", 400)
        return UserTokens(
            access_token=body["access_token"],
            access_expires_at=_expiry(body.get("expires_in")),
            refresh_token=body.get("refresh_token"),
            refresh_expires_at=_expiry(body.get("refresh_token_expires_in")),
        )

    async def exchange_code(self, code: str, code_verifier: str) -> UserTokens:
        return await self._token_request(
            {"code": code, "redirect_uri": self.redirect_uri, "code_verifier": code_verifier}
        )

    async def refresh(self, refresh_token: str) -> UserTokens:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def get_user(self, token: str) -> GitHubUser:
        resp = await self.http.get(f"{self.api}/user", headers=self._headers(token), timeout=15.0)
        if resp.status_code != 200:
            raise GitHubError("Could not read the GitHub user.", resp.status_code)
        body = resp.json()
        return GitHubUser(
            id=int(body["id"]), login=str(body["login"]), avatar_url=body.get("avatar_url")
        )

    async def _paged(self, url: str, token: str, key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = await self.http.get(
                url,
                params={"per_page": 100, "page": page},
                headers=self._headers(token),
                timeout=20.0,
            )
            if resp.status_code == 401:
                raise GitHubError("GitHub authorization expired.", 401)
            if resp.status_code != 200:
                raise GitHubError("GitHub API request failed.", resp.status_code)
            body = resp.json()
            batch = body.get(key, [])
            items.extend(batch)
            total = int(body.get("total_count", len(items)))
            if not batch or len(items) >= total or len(items) >= MAX_REPOS:
                return items
            page += 1

    async def installations(self, token: str) -> list[Installation]:
        raw = await self._paged(f"{self.api}/user/installations", token, "installations")
        out = []
        for i in raw:
            account = i.get("account") or {}
            out.append(
                Installation(
                    id=int(i["id"]),
                    account_login=str(account.get("login", "")),
                    account_type=account.get("type"),
                )
            )
        return out

    async def installation_repos(self, token: str, installation_id: int) -> list[RepoAccess]:
        raw = await self._paged(
            f"{self.api}/user/installations/{installation_id}/repositories", token, "repositories"
        )
        out = []
        for r in raw:
            out.append(
                RepoAccess(
                    repo_id=int(r["id"]),
                    owner=str((r.get("owner") or {}).get("login") or r["full_name"].split("/")[0]),
                    name=str(r["name"]),
                    private=bool(r.get("private", True)),
                    default_branch=str(r.get("default_branch") or "main"),
                    permission=_permission(r.get("permissions")),
                    installation_id=installation_id,
                )
            )
        return out

    # ---- App-level (JWT) calls ----------------------------------------------------

    def app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": str(self.s.github_app_id)},
            self.s.secret("github_app_private_key"),
            algorithm="RS256",
        )

    async def repo_installation_id(self, owner: str, repo: str) -> int | None:
        """The App installation covering ``owner/repo``, or None when it isn't installed."""
        if not (self.s.github_app_id and self.s.github_app_private_key):
            return None
        resp = await self.http.get(
            f"{self.api}/repos/{owner}/{repo}/installation",
            headers=self._headers(self.app_jwt(), bearer=True),
            timeout=15.0,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubError("GitHub API request failed.", resp.status_code)
        return int(resp.json()["id"])

    async def revoke_grant(self, access_token: str) -> None:
        """Best effort: revoke the user's authorization of the App (on account deletion)."""
        if not self.s.github_oauth_configured:
            return
        await self.http.request(
            "DELETE",
            f"{self.api}/applications/{self.s.github_app_client_id}/grant",
            json={"access_token": access_token},
            auth=(self.s.github_app_client_id or "", self.s.secret("github_app_client_secret")),
            headers=self._headers(),
            timeout=10.0,
        )
