from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID

import httpx

from backend.schemas.post import PostCreate

from .base_parser import BaseParser


class ConnectedSourceFetchError(RuntimeError):
    """Raised when a connected provider cannot return account data."""


class _TokenApiClient(BaseParser):
    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "shadowself-profiler/0.1",
        }

    def _error_message(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text or response.reason_phrase
        return str(body.get("error") or body.get("message") or body)

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict | None = None,
    ) -> dict:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = self._error_message(exc.response)
            raise ConnectedSourceFetchError(message) from exc
        except httpx.HTTPError as exc:
            raise ConnectedSourceFetchError(str(exc)) from exc
        return response.json()

    def _make_post(
        self,
        *,
        user_id: UUID,
        source: str,
        content: str,
        posted_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> PostCreate | None:
        normalized = self.normalize_text(content)
        if not self.is_meaningful(normalized):
            return None
        post = PostCreate(
            user_id=user_id,
            source=source,
            content=normalized,
            posted_at=posted_at,
            metadata=metadata or {},
        )
        post.content_hash = self.generate_hash(post.content)
        return post

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Gmail
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class GmailApiClient(_TokenApiClient):
    """Fetch recent sent Gmail messages with a short-lived OAuth access token."""

    async def parse(
        self,
        access_token: str,
        user_id: UUID,
        max_results: int = 25,
    ) -> AsyncGenerator[PostCreate, None]:
        async with httpx.AsyncClient(headers=self._headers(access_token), timeout=20.0) as client:
            listing = await self._get_json(
                client,
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"maxResults": max_results, "q": "in:sent -in:spam -in:trash"},
            )
            for item in listing.get("messages", []):
                message = await self._get_json(
                    client,
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}",
                    params={"format": "full"},
                )
                post = self._message_to_post(message, user_id)
                if post:
                    yield post

    def _message_to_post(self, message: dict, user_id: UUID) -> PostCreate | None:
        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in message.get("payload", {}).get("headers", [])
        }
        subject = headers.get("subject", "(No subject)")
        body = self._extract_body(message.get("payload", {}))
        content = f"Email subject: {subject}\n\n{body}"
        internal_date = message.get("internalDate")
        posted_at = None
        if internal_date:
            posted_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        return self._make_post(
            user_id=user_id,
            source="gmail",
            content=content,
            posted_at=posted_at,
            metadata={
                "platform": "gmail",
                "message_id": message.get("id"),
                "thread_id": message.get("threadId"),
                "subject": subject,
                "from": headers.get("from"),
                "to": headers.get("to"),
                "labels": message.get("labelIds", []),
            },
        )

    def _extract_body(self, payload: dict) -> str:
        if payload.get("mimeType") == "text/plain":
            return self._decode(payload.get("body", {}).get("data", ""))
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                return self._decode(part.get("body", {}).get("data", ""))
            nested = self._extract_body(part)
            if nested:
                return nested
        return ""

    def _decode(self, value: str) -> str:
        if not value:
            return ""
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="ignore")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# LinkedIn â€” YOUR posts, comments, and profile via OAuth token
#
# Required OAuth scopes (set in your LinkedIn app):
#   openid, profile, email          â†’ profile info
#   w_member_social                 â†’ your posts (UGC posts API)
#
# The LinkedIn API returns YOUR posts only â€” there is no API endpoint
# that returns another person's posts without special partner access.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class LinkedInApiClient(_TokenApiClient):
    """
    Fetches YOUR LinkedIn profile + posts + comments using your OAuth token.
    Requires scopes: openid, profile, w_member_social
    """

    async def parse(
        self,
        access_token: str,
        user_id: UUID,
        max_results: int = 50,
    ) -> AsyncGenerator[PostCreate, None]:
        async with httpx.AsyncClient(
            headers=self._headers(access_token), timeout=20.0
        ) as client:

            # 1. Profile info (OpenID Connect userinfo)
            try:
                profile = await self._get_json(client, "https://api.linkedin.com/v2/userinfo")
                name = profile.get("name") or profile.get("given_name") or "LinkedIn User"
                sub = profile.get("sub")  # LinkedIn member URN ID
                headline = profile.get("headline") or ""
                locale = profile.get("locale") or ""

                content_parts = [f"LinkedIn profile for {name}."]
                if headline:
                    content_parts.append(f"Headline: {headline}.")
                if locale:
                    content_parts.append(f"Locale: {locale}.")

                profile_post = self._make_post(
                    user_id=user_id,
                    source="linkedin",
                    content=" ".join(content_parts),
                    metadata={
                        "platform": "linkedin",
                        "source_type": "linkedin_profile",
                        "sub": sub,
                        "name": name,
                        "email": profile.get("email"),
                    },
                )
                if profile_post:
                    yield profile_post

            except ConnectedSourceFetchError as exc:
                print(f"[LinkedIn] profile fetch failed: {exc}")
                sub = None
                name = "LinkedIn User"

            # 2. Your UGC posts (text posts, articles, shares)
            # Uses the UGC Posts API â€” returns only YOUR posts
            try:
                # Build author URN from sub (format: urn:li:person:{id})
                author_urn = f"urn:li:person:{sub}" if sub else None
                if not author_urn:
                    print("[LinkedIn] no sub/URN â€” skipping posts fetch")
                    return

                ugc_url = "https://api.linkedin.com/v2/ugcPosts"
                params = {
                    "q": "authors",
                    "authors": f"List({author_urn})",
                    "count": min(max_results, 100),
                    "sortBy": "LAST_MODIFIED",
                }
                ugc_data = await self._get_json(client, ugc_url, params=params)
                elements = ugc_data.get("elements", [])
                print(f"[LinkedIn] found {len(elements)} UGC posts")

                for item in elements:
                    post = self._ugc_to_post(item, user_id, name)
                    if post:
                        yield post

            except ConnectedSourceFetchError as exc:
                print(f"[LinkedIn] UGC posts fetch failed: {exc}")
                # Fall back to shares API (older endpoint, broader support)
                try:
                    shares_url = "https://api.linkedin.com/v2/shares"
                    params = {
                        "q": "owners",
                        "owners": f"urn:li:person:{sub}",
                        "count": min(max_results, 100),
                    }
                    shares_data = await self._get_json(client, shares_url, params=params)
                    elements = shares_data.get("elements", [])
                    print(f"[LinkedIn] fallback shares: found {len(elements)} items")
                    for item in elements:
                        post = self._share_to_post(item, user_id, name)
                        if post:
                            yield post
                except ConnectedSourceFetchError as exc2:
                    print(f"[LinkedIn] shares fallback also failed: {exc2}")

    def _ugc_to_post(self, item: dict, user_id: UUID, author_name: str) -> PostCreate | None:
        try:
            # Extract text from specificContent â†’ com.linkedin.ugc.ShareContent
            share = item.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {})
            commentary = share.get("shareCommentary", {}).get("text", "").strip()
            media = share.get("shareMediaCategory", "")

            # Article/external content may have a title
            media_items = share.get("media", [])
            article_title = ""
            article_desc = ""
            if media_items:
                m = media_items[0]
                orig = m.get("originalUrl", "")
                article_title = m.get("title", {}).get("text", "").strip() if isinstance(m.get("title"), dict) else ""
                article_desc = m.get("description", {}).get("text", "").strip() if isinstance(m.get("description"), dict) else ""

            parts = []
            if commentary:
                parts.append(commentary)
            if article_title and article_title not in commentary:
                parts.append(f"Shared: {article_title}")
            if article_desc:
                parts.append(article_desc)

            raw = "\n\n".join(parts)
            if not raw.strip():
                return None

            created = item.get("created", {}).get("time")
            posted_at = (
                datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                if created else None
            )

            return self._make_post(
                user_id=user_id,
                source="linkedin",
                content=raw,
                posted_at=posted_at,
                metadata={
                    "platform": "linkedin",
                    "source_type": "linkedin_post",
                    "post_id": item.get("id"),
                    "author": author_name,
                    "media_category": media,
                },
            )
        except Exception as exc:
            print(f"[LinkedIn] skipped UGC item: {exc}")
            return None

    def _share_to_post(self, item: dict, user_id: UUID, author_name: str) -> PostCreate | None:
        try:
            text = item.get("text", {}).get("text", "").strip()
            content_block = item.get("content", {})
            title = content_block.get("title", "").strip()
            desc = content_block.get("description", "").strip()

            parts = []
            if text:
                parts.append(text)
            if title and title not in text:
                parts.append(f"Shared: {title}")
            if desc:
                parts.append(desc)

            raw = "\n\n".join(parts)
            if not raw.strip():
                return None

            created = item.get("created", {}).get("time")
            posted_at = (
                datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                if created else None
            )

            return self._make_post(
                user_id=user_id,
                source="linkedin",
                content=raw,
                posted_at=posted_at,
                metadata={
                    "platform": "linkedin",
                    "source_type": "linkedin_share",
                    "share_id": item.get("id"),
                    "author": author_name,
                },
            )
        except Exception as exc:
            print(f"[LinkedIn] skipped share item: {exc}")
            return None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Meta (Facebook + Instagram) â€” YOUR posts via Graph API OAuth token
#
# Required permissions (request in your Meta app):
#   public_profile               â†’ basic profile
#   user_posts                   â†’ your Facebook posts
#   user_status                  â†’ your status updates
#   user_tagged_places           â†’ tagged posts (optional)
#   instagram_basic              â†’ Instagram profile + media
#   instagram_content_publish    â†’ Instagram posts (read)
#
# Only returns YOUR content â€” no way to read other users' posts
# via the API since the 2018 Cambridge Analytica policy changes.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class MetaGraphApiClient(_TokenApiClient):
    """
    Fetches YOUR Facebook posts + Instagram posts using your Graph API token.
    Only returns content you authored â€” no access to other users' posts.
    """

    _GRAPH = "https://graph.facebook.com/v19.0"

    async def parse(
        self,
        access_token: str,
        user_id: UUID,
        max_results: int = 50,
    ) -> AsyncGenerator[PostCreate, None]:
        async with httpx.AsyncClient(
            headers=self._headers(access_token), timeout=20.0
        ) as client:

            # â”€â”€ Facebook profile + posts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            try:
                profile = await self._get_json(
                    client,
                    f"{self._GRAPH}/me",
                    params={"fields": "id,name,about,bio", "access_token": access_token},
                )
                fb_id = profile.get("id")
                fb_name = profile.get("name", "Facebook User")

                # Profile summary post
                about = profile.get("about") or profile.get("bio") or ""
                profile_content = f"Facebook profile for {fb_name}."
                if about:
                    profile_content += f" About: {about}."
                p = self._make_post(
                    user_id=user_id,
                    source="meta",
                    content=profile_content,
                    metadata={
                        "platform": "facebook",
                        "source_type": "facebook_profile",
                        "profile_id": fb_id,
                        "name": fb_name,
                    },
                )
                if p:
                    yield p

                # YOUR Facebook posts (paginated)
                async for post in self._fetch_fb_posts(
                    client, access_token, fb_id, user_id, max_results
                ):
                    yield post

            except ConnectedSourceFetchError as exc:
                print(f"[Meta] Facebook profile/posts failed: {exc}")

            # â”€â”€ Instagram posts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            try:
                async for post in self._fetch_instagram_posts(
                    client, access_token, user_id, max_results
                ):
                    yield post
            except ConnectedSourceFetchError as exc:
                print(f"[Meta] Instagram fetch failed: {exc}")

    async def _fetch_fb_posts(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        fb_id: str | None,
        user_id: UUID,
        max_results: int,
    ) -> AsyncGenerator[PostCreate, None]:
        """Paginate through YOUR Facebook posts using the /me/posts edge."""
        url = f"{self._GRAPH}/me/posts"
        params = {
            "fields": "id,message,story,created_time,permalink_url,full_picture",
            "limit": min(max_results, 100),
            "access_token": access_token,
        }
        fetched = 0

        while url and fetched < max_results:
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                print(f"[Meta] FB posts page error: {exc}")
                break

            for item in data.get("data", []):
                content = item.get("message") or item.get("story") or ""
                if not content.strip():
                    continue
                p = self._make_post(
                    user_id=user_id,
                    source="meta",
                    content=content,
                    posted_at=self._parse_datetime(item.get("created_time")),
                    metadata={
                        "platform": "facebook",
                        "source_type": "facebook_post",
                        "post_id": item.get("id"),
                        "url": item.get("permalink_url"),
                    },
                )
                if p:
                    yield p
                    fetched += 1
                    if fetched >= max_results:
                        return

            # Follow pagination cursor
            next_url = data.get("paging", {}).get("next")
            if not next_url:
                break
            url = next_url
            params = {}  # next URL already includes all params

        print(f"[Meta] fetched {fetched} Facebook posts")

    async def _fetch_instagram_posts(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        user_id: UUID,
        max_results: int,
    ) -> AsyncGenerator[PostCreate, None]:
        """
        Fetch YOUR Instagram media captions via the Instagram Basic Display API.
        Requires instagram_basic permission on your Meta app.
        """
        # First get the Instagram account ID linked to this token
        try:
            ig_me = await self._get_json(
                client,
                "https://graph.instagram.com/me",
                params={
                    "fields": "id,username,account_type",
                    "access_token": access_token,
                },
            )
        except ConnectedSourceFetchError:
            # Try via Facebook Graph (if using Facebook token with IG linked)
            try:
                ig_accounts = await self._get_json(
                    client,
                    f"{self._GRAPH}/me/accounts",
                    params={"access_token": access_token},
                )
                # Look for Instagram business account
                page_token = None
                page_id = None
                for page in ig_accounts.get("data", []):
                    page_id = page.get("id")
                    page_token = page.get("access_token")
                    break
                if not page_id:
                    print("[Meta] No Instagram account found via Facebook pages")
                    return
                ig_biz = await self._get_json(
                    client,
                    f"{self._GRAPH}/{page_id}",
                    params={
                        "fields": "instagram_business_account",
                        "access_token": page_token or access_token,
                    },
                )
                ig_id = ig_biz.get("instagram_business_account", {}).get("id")
                if not ig_id:
                    print("[Meta] No linked Instagram business account")
                    return
                ig_me = {"id": ig_id, "username": "unknown"}
            except Exception as exc:
                print(f"[Meta] Instagram account lookup failed: {exc}")
                return

        ig_id = ig_me.get("id")
        ig_username = ig_me.get("username", "")
        print(f"[Meta] Instagram account: @{ig_username} ({ig_id})")

        # Fetch YOUR media (photos, videos, carousels) â€” captions are the text content
        try:
            media_data = await self._get_json(
                client,
                f"https://graph.instagram.com/{ig_id}/media",
                params={
                    "fields": "id,caption,media_type,timestamp,permalink",
                    "limit": min(max_results, 100),
                    "access_token": access_token,
                },
            )
        except ConnectedSourceFetchError as exc:
            print(f"[Meta] Instagram media fetch failed: {exc}")
            return

        fetched = 0
        for item in media_data.get("data", []):
            caption = (item.get("caption") or "").strip()
            if not caption:
                continue
            p = self._make_post(
                user_id=user_id,
                source="meta",
                content=caption,
                posted_at=self._parse_datetime(item.get("timestamp")),
                metadata={
                    "platform": "instagram",
                    "source_type": "instagram_post",
                    "media_type": item.get("media_type"),
                    "post_id": item.get("id"),
                    "url": item.get("permalink"),
                    "username": ig_username,
                },
            )
            if p:
                yield p
                fetched += 1
                if fetched >= max_results:
                    break

        print(f"[Meta] fetched {fetched} Instagram captions")

