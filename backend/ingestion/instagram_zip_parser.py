"""
Instagram ZIP export parser.

How to get: Instagram → Accounts Centre → Your information and permissions
            → Download your information → Select account → All time → JSON
            Download arrives within minutes to hours as a ZIP.

Key files inside the ZIP:
  - content/posts_1.json     → your photo/video captions
  - content/stories.json     → story captions (optional signal)
  - comments/post_comments_1.json  → your comments on others' posts
"""

import json
import zipfile
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID

from .base_parser import BaseParser
from ..schemas.post import PostCreate

_SKIP = {"", "[removed]", "[deleted]"}


def _fix_encoding(s: str) -> str:
    """Fix Instagram's broken UTF-8 in JSON exports (same issue as Facebook)."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _ts(v) -> datetime | None:
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (ValueError, OSError):
        return None


class InstagramZipParser(BaseParser):
    """Parses Instagram's GDPR data export ZIP (JSON format)."""

    async def parse(self, zip_path: str, user_id: UUID) -> AsyncGenerator[PostCreate, None]:
        count = 0
        with zipfile.ZipFile(zip_path) as z:
            print(f"[InstagramZipParser] files: {[n for n in z.namelist() if n.endswith('.json')][:20]}")

            for zname in z.namelist():
                lower = zname.lower()

                # Posts: content/posts_1.json
                if "posts_" in lower and lower.endswith(".json") and "comment" not in lower:
                    async for p in self._parse_posts(z, zname, user_id):
                        yield p; count += 1

                # Stories: content/stories.json
                elif "stories" in lower and lower.endswith(".json"):
                    async for p in self._parse_stories(z, zname, user_id):
                        yield p; count += 1

                # Comments: comments/post_comments_1.json
                elif "post_comments" in lower and lower.endswith(".json"):
                    async for p in self._parse_comments(z, zname, user_id):
                        yield p; count += 1

        print(f"[InstagramZipParser] done — {count} items")

    async def _parse_posts(self, z, fname, user_id):
        data = self._load_json(z, fname)
        # Structure: list of {media: [{title: "caption", creation_timestamp: ..., uri: ...}]}
        items = data if isinstance(data, list) else []

        for item in items:
            media_list = item.get("media", [])
            if not isinstance(media_list, list):
                media_list = [item]

            for media in media_list:
                caption = _fix_encoding(media.get("title") or "").strip()
                if not caption or caption.lower() in _SKIP:
                    continue

                content = self.normalize_text(caption)
                if not self.is_meaningful(content):
                    continue

                post = PostCreate(
                    user_id=user_id,
                    source="meta",
                    content=content,
                    posted_at=_ts(media.get("creation_timestamp")),
                    metadata={
                        "platform": "instagram",
                        "source_type": "instagram_post",
                        "via": "zip_export",
                        "uri": media.get("uri", ""),
                    },
                )
                post.content_hash = self.generate_hash(post.content)
                yield post

    async def _parse_stories(self, z, fname, user_id):
        data = self._load_json(z, fname)
        # Structure: {ig_stories: [{title: "caption", creation_timestamp: ...}]}
        items = data.get("ig_stories", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return

        for item in items:
            caption = _fix_encoding(item.get("title") or "").strip()
            if not caption or caption.lower() in _SKIP:
                continue

            content = self.normalize_text(caption)
            if not self.is_meaningful(content):
                continue

            post = PostCreate(
                user_id=user_id,
                source="meta",
                content=content,
                posted_at=_ts(item.get("creation_timestamp")),
                metadata={
                    "platform": "instagram",
                    "source_type": "instagram_story",
                    "via": "zip_export",
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    async def _parse_comments(self, z, fname, user_id):
        data = self._load_json(z, fname)
        # Structure: {comments_media_comments: [{string_map_data: {Text: {value: "..."}, Time: {timestamp: ...}}}]}
        key = next((k for k in data if "comment" in k.lower()), None) if isinstance(data, dict) else None
        items = data.get(key, data) if key else (data if isinstance(data, list) else [])

        for item in items:
            smd = item.get("string_map_data", {})
            text_node = smd.get("Comment") or smd.get("Text") or {}
            text = _fix_encoding(text_node.get("value") or "").strip()
            if not text or text.lower() in _SKIP:
                continue

            ts_node = smd.get("Time") or {}
            posted_at = _ts(ts_node.get("timestamp"))

            content = self.normalize_text(text)
            if not self.is_meaningful(content):
                continue

            post = PostCreate(
                user_id=user_id,
                source="meta",
                content=content,
                posted_at=posted_at,
                metadata={
                    "platform": "instagram",
                    "source_type": "instagram_comment",
                    "via": "zip_export",
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    def _load_json(self, z: zipfile.ZipFile, fname: str) -> dict | list:
        try:
            with z.open(fname) as f:
                return json.loads(f.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            print(f"[InstagramZipParser] could not read {fname}: {exc}")
            return []
