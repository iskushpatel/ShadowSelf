from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import AsyncGenerator
from uuid import UUID

import httpx

from backend.schemas.post import PostCreate

from .base_parser import BaseParser


_GITHUB_BASE = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "shadowself-profiler/0.1",
}
_MAX_REPOS = 30
_MAX_EVENTS = 50


class GithubFetchError(RuntimeError):
    """Raised when GitHub cannot return public account data."""


class GithubPublicClient(BaseParser):
    """Fetch public GitHub profile signals without OAuth."""

    def __init__(self) -> None:
        self.headers = dict(_HEADERS)
        token = os.getenv("GITHUB_TOKEN")
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def parse(
        self,
        username: str,
        user_id: UUID,
    ) -> AsyncGenerator[PostCreate, None]:
        username = username.strip().lstrip("@")
        async with httpx.AsyncClient(headers=self.headers, timeout=15.0) as client:
            profile = await self._get_json(client, f"/users/{username}")
            if not profile:
                return

            profile_post = self._profile_to_post(profile, user_id)
            if profile_post:
                yield profile_post

            repos = await self._get_json(
                client,
                f"/users/{username}/repos",
                params={
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": _MAX_REPOS,
                    "type": "owner",
                },
            )
            for repo in repos or []:
                post = self._repo_to_post(repo, user_id)
                if post:
                    yield post

            events = await self._get_json(
                client,
                f"/users/{username}/events/public",
                params={"per_page": _MAX_EVENTS},
            )
            for event in events or []:
                post = self._event_to_post(event, user_id)
                if post:
                    yield post

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict | None = None,
    ) -> dict | list | None:
        try:
            response = await client.get(f"{_GITHUB_BASE}{path}", params=params)
        except httpx.HTTPError as exc:
            raise GithubFetchError(f"GitHub request failed: {exc}") from exc

        if response.status_code == 404:
            return None
        if response.status_code in {403, 429}:
            message = self._error_message(response)
            raise GithubFetchError(
                f"GitHub API limit/error: {message}. Add GITHUB_TOKEN to .env and restart the server."
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = self._error_message(response)
            raise GithubFetchError(f"GitHub API error: {message}") from exc
        return response.json()

    def _error_message(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text or response.reason_phrase

        if isinstance(body, dict):
            return str(body.get("message") or body)
        return str(body)

    def _profile_to_post(self, profile: dict, user_id: UUID) -> PostCreate | None:
        parts = [
            f"GitHub profile for {profile.get('login')}",
            profile.get("bio") or "",
            f"Company: {profile.get('company')}" if profile.get("company") else "",
            f"Public repos: {profile.get('public_repos')}",
            f"Followers: {profile.get('followers')}",
        ]
        return self._make_post(
            user_id=user_id,
            content="\n".join(part for part in parts if part),
            posted_at=self._parse_datetime(profile.get("updated_at")),
            metadata={
                "platform": "github",
                "source_type": "github_profile",
                "username": profile.get("login"),
                "url": profile.get("html_url"),
            },
        )

    def _repo_to_post(self, repo: dict, user_id: UUID) -> PostCreate | None:
        language = repo.get("language")
        topics = repo.get("topics") or []
        content = (
            f"Repository {repo.get('name')}. "
            f"{repo.get('description') or ''} "
            f"Primary language: {language or 'unknown'}. "
            f"Topics: {', '.join(topics) if topics else 'none'}. "
            f"Stars: {repo.get('stargazers_count')}. Forks: {repo.get('forks_count')}."
        )
        return self._make_post(
            user_id=user_id,
            content=content,
            posted_at=self._parse_datetime(repo.get("updated_at")),
            metadata={
                "platform": "github",
                "source_type": "github_repo",
                "username": repo.get("owner", {}).get("login"),
                "repo": repo.get("name"),
                "language": language,
                "topics": topics,
                "url": repo.get("html_url"),
            },
        )

    def _event_to_post(self, event: dict, user_id: UUID) -> PostCreate | None:
        event_type = event.get("type")
        repo_name = (event.get("repo") or {}).get("name")
        content = f"GitHub activity: {event_type} in {repo_name}."

        payload = event.get("payload") or {}
        commits = payload.get("commits") or []
        if commits:
            messages = [commit.get("message", "") for commit in commits[:3]]
            content = f"{content} Recent commit messages: {' | '.join(messages)}"

        return self._make_post(
            user_id=user_id,
            content=content,
            posted_at=self._parse_datetime(event.get("created_at")),
            metadata={
                "platform": "github",
                "source_type": "github_event",
                "event_type": event_type,
                "repo": repo_name,
                "url": f"https://github.com/{repo_name}" if repo_name else None,
            },
        )

    def _make_post(
        self,
        *,
        user_id: UUID,
        content: str,
        posted_at: datetime | None,
        metadata: dict,
    ) -> PostCreate | None:
        normalized = self.normalize_text(content)
        if not self.is_meaningful(normalized):
            return None
        post = PostCreate(
            user_id=user_id,
            source="github",
            content=normalized,
            posted_at=posted_at,
            metadata={key: value for key, value in metadata.items() if value is not None},
        )
        post.content_hash = self.generate_hash(post.content)
        return post

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
