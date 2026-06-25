"""
Reddit live client — RSS feed strategy.

Simply appends .rss to the Reddit user profile URL:
  https://www.reddit.com/user/{username}.rss

No API key, no OAuth, no credentials. Reddit's RSS feeds are public,
machine-readable, and bypass the JSON API bot checks entirely.

Limitations:
  - Returns ~25 most recent items per feed (Reddit's RSS cap)
  - Covers both posts and comments in a single feed
  - No pagination (RSS is not paginated)

For users with more history, we hit both:
  - /user/{username}.rss          → recent activity (posts + comments mixed)
  - /user/{username}/submitted.rss → posts only
  - /user/{username}/comments.rss  → comments only
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncGenerator
from uuid import UUID
from xml.etree import ElementTree as ET

import httpx

from .base_parser import BaseParser
from ..schemas.post import PostCreate

_RSS_BASE = "https://www.reddit.com"
_SKIP_CONTENT = {"[removed]", "[deleted]", ""}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
_ua_index = 0


def _next_ua() -> str:
    global _ua_index
    ua = _USER_AGENTS[_ua_index % len(_USER_AGENTS)]
    _ua_index += 1
    return ua


# Atom namespace used by Reddit's RSS feeds
_ATOM_NS = "http://www.w3.org/2005/Atom"
_NS = {
    "atom": _ATOM_NS,
    "media": "http://search.yahoo.com/mrss/",
}


class RedditLiveClient(BaseParser):
    """
    Fetches a Reddit user's public posts + comments via RSS feeds.
    No credentials required.
    """

    async def parse(self, username: str, user_id: UUID) -> AsyncGenerator[PostCreate, None]:
        username = username.strip().lstrip("u/").lstrip("/").strip()
        if not username:
            print("[RedditLiveClient] ERROR: empty username")
            return

        print(f"[RedditLiveClient] starting RSS fetch for u/{username}")

        seen_ids: set[str] = set()
        count = 0

        # Hit all three feeds to maximise coverage
        feeds = [
            (f"{_RSS_BASE}/user/{username}/comments.rss", "comments"),
            (f"{_RSS_BASE}/user/{username}/submitted.rss", "submitted"),
            (f"{_RSS_BASE}/user/{username}.rss", "overview"),
        ]

        for feed_url, feed_kind in feeds:
            async for post in self._fetch_rss(feed_url, feed_kind, user_id, seen_ids):
                yield post
                count += 1

            # Small delay between feeds
            await asyncio.sleep(1.0)

        print(f"[RedditLiveClient] DONE — {count} items for u/{username}")

    async def _fetch_rss(
        self,
        url: str,
        feed_kind: str,
        user_id: UUID,
        seen_ids: set[str],
    ) -> AsyncGenerator[PostCreate, None]:

        headers = {
            "User-Agent": _next_ua(),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.reddit.com/",
        }

        async with httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                print(f"[RedditLiveClient] [{feed_kind}] NETWORK ERROR: {exc}")
                return

            print(f"[RedditLiveClient] [{feed_kind}] HTTP {resp.status_code} — {url}")

            if resp.status_code == 404:
                print(f"[RedditLiveClient] [{feed_kind}] 404 — feed not found")
                return
            if resp.status_code == 403:
                print(f"[RedditLiveClient] [{feed_kind}] 403 — account private/suspended")
                return
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "15"))
                print(f"[RedditLiveClient] [{feed_kind}] rate limited — sleeping {wait}s")
                await asyncio.sleep(wait)
                return

            if resp.status_code != 200:
                print(f"[RedditLiveClient] [{feed_kind}] unexpected status {resp.status_code}")
                return

            # Parse XML
            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError as exc:
                print(f"[RedditLiveClient] [{feed_kind}] XML parse error: {exc}")
                print(f"[RedditLiveClient] [{feed_kind}] raw preview: {resp.text[:300]}")
                return

            # Reddit RSS is Atom format: <feed> with <entry> children
            entries = root.findall("atom:entry", _NS)
            if not entries:
                # Fallback: try plain RSS <item> tags
                entries = root.findall(".//item")

            print(f"[RedditLiveClient] [{feed_kind}] {len(entries)} entries found")

            for entry in entries:
                post = self._entry_to_post(entry, feed_kind, user_id, seen_ids)
                if post:
                    yield post

    def _entry_to_post(
        self,
        entry: ET.Element,
        feed_kind: str,
        user_id: UUID,
        seen_ids: set[str],
    ) -> PostCreate | None:
        try:
            # Deduplicate by entry id
            entry_id = self._text(entry, "atom:id") or self._text(entry, "guid") or ""
            if entry_id in seen_ids:
                return None
            seen_ids.add(entry_id)

            # Title
            title = (self._text(entry, "atom:title") or self._text(entry, "title") or "").strip()

            # Content — Reddit puts the HTML body in <content> or <summary>
            content_html = (
                self._text(entry, "atom:content")
                or self._text(entry, "atom:summary")
                or self._text(entry, "description")
                or ""
            ).strip()

            # Strip HTML tags for plain text
            raw = self._strip_html(content_html) or title
            raw = raw.strip()

            if not raw or raw.lower() in _SKIP_CONTENT:
                return None

            # If the entry is a post (not a comment), prepend title
            if feed_kind == "submitted" and title and title not in raw:
                raw = f"{title}\n\n{raw}"

            content = self.normalize_text(raw)
            if not self.is_meaningful(content):
                return None

            # Determine source type from feed or entry content
            link = (
                self._text(entry, "atom:link[@rel='alternate']")
                or self._attr(entry, "atom:link", "href")
                or self._text(entry, "link")
                or entry_id
            )
            if "/comments/" in link and feed_kind != "submitted":
                source = "reddit_comment"
            else:
                source = "reddit_post"

            # Published date
            published_str = (
                self._text(entry, "atom:published")
                or self._text(entry, "atom:updated")
                or self._text(entry, "pubDate")
                or ""
            )
            posted_at = self._parse_date(published_str)

            # Subreddit from category tag
            category = entry.find("atom:category", _NS)
            subreddit = category.get("term") if category is not None else None

            # Author
            author_el = entry.find("atom:author/atom:name", _NS)
            author = author_el.text.strip() if author_el is not None else None

            meta: dict = {"platform": "reddit", "source_type": source}
            if subreddit:
                meta["subreddit"] = subreddit
            if author:
                meta["author"] = author
            if link:
                meta["url"] = link
            meta["feed"] = feed_kind

            post = PostCreate(
                user_id=user_id,
                source=source,
                content=content,
                posted_at=posted_at,
                metadata=meta,
            )
            post.content_hash = self.generate_hash(post.content)
            return post

        except Exception as exc:
            print(f"[RedditLiveClient] skipped entry: {exc}")
            return None

    # ─── XML helpers ────────────────────────────────────────────────────────

    def _text(self, el: ET.Element, tag: str) -> str | None:
        """Find a child element and return its text, trying both namespaced and plain."""
        # Try with namespace
        found = el.find(tag, _NS)
        if found is not None and found.text:
            return found.text.strip()
        # Try plain tag name (strip prefix)
        plain = tag.split(":")[-1] if ":" in tag else tag
        found = el.find(plain)
        if found is not None and found.text:
            return found.text.strip()
        return None

    def _attr(self, el: ET.Element, tag: str, attr: str) -> str | None:
        """Find element and return an attribute value."""
        found = el.find(tag, _NS)
        if found is not None:
            return found.get(attr)
        return None

    def _strip_html(self, html: str) -> str:
        """Very lightweight HTML stripper using ElementTree."""
        try:
            # Wrap in a root tag so ET can parse fragments
            wrapped = f"<root>{html}</root>"
            root = ET.fromstring(wrapped)
            return " ".join(root.itertext()).strip()
        except ET.ParseError:
            # Fallback: remove tags with simple replacement
            import re
            clean = re.sub(r"<[^>]+>", " ", html)
            clean = re.sub(r"\s+", " ", clean)
            return clean.strip()

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        # Try ISO 8601 (Atom format)
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Try RFC 2822 (RSS format)
        try:
            return parsedate_to_datetime(date_str).astimezone(timezone.utc)
        except Exception:
            pass
        return None
