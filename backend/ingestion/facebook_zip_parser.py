"""
Facebook ZIP export parser.

How to get: Facebook → Settings → Your Facebook Information
            → Download your information → Select "Posts", "Comments"
            → Format: JSON → Date range: All time → Create file

Key files inside the ZIP:
  - your_posts/your_posts_1.json   → your posts
  - comments/comments.json          → your comments on others' posts
  - messages/ (optional)            → DMs — not parsed by default
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
    """Fix Facebook's broken UTF-8 encoding in JSON exports."""
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


class FacebookZipParser(BaseParser):
    """Parses Facebook's GDPR data export ZIP (JSON format)."""

    async def parse(self, zip_path: str, user_id: UUID) -> AsyncGenerator[PostCreate, None]:
        count = 0
        with zipfile.ZipFile(zip_path) as z:
            print(f"[FacebookZipParser] files: {[n for n in z.namelist() if n.endswith('.json')][:20]}")

            for zname in z.namelist():
                lower = zname.lower()

                # Posts: your_posts/your_posts_1.json, your_posts_1.json, etc.
                if "your_posts" in lower and lower.endswith(".json"):
                    async for p in self._parse_posts(z, zname, user_id):
                        yield p; count += 1

                # Comments: comments/comments.json
                elif "comments" in lower and lower.endswith(".json") and "message" not in lower:
                    async for p in self._parse_comments(z, zname, user_id):
                        yield p; count += 1

        print(f"[FacebookZipParser] done — {count} items")

    async def _parse_posts(self, z, fname, user_id):
        data = self._load_json(z, fname)
        if not isinstance(data, list):
            data = data.get("status_updates") or data.get("posts") or []

        for item in data:
            # Facebook post structure: {data: [{post: "text"}], timestamp: 1234}
            text = ""
            data_arr = item.get("data", [])
            if isinstance(data_arr, list):
                for d in data_arr:
                    text = _fix_encoding(d.get("post") or d.get("update_timestamp") or "").strip()
                    if text:
                        break

            # Some exports have title at top level
            if not text:
                text = _fix_encoding(item.get("title") or "").strip()

            if not text or text.lower() in _SKIP:
                continue

            content = self.normalize_text(text)
            if not self.is_meaningful(content):
                continue

            post = PostCreate(
                user_id=user_id,
                source="meta",
                content=content,
                posted_at=_ts(item.get("timestamp")),
                metadata={
                    "platform": "facebook",
                    "source_type": "facebook_post",
                    "via": "zip_export",
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    async def _parse_comments(self, z, fname, user_id):
        data = self._load_json(z, fname)
        # Structure: {comments: [{data: [{comment: {comment: "text"}}], timestamp: ..., title: ...}]}
        items = data if isinstance(data, list) else data.get("comments", [])

        for item in items:
            text = ""
            data_arr = item.get("data", [])
            if isinstance(data_arr, list):
                for d in data_arr:
                    c = d.get("comment", {})
                    text = _fix_encoding(c.get("comment") or "").strip()
                    if text:
                        break

            if not text or text.lower() in _SKIP:
                continue

            content = self.normalize_text(text)
            if not self.is_meaningful(content):
                continue

            post = PostCreate(
                user_id=user_id,
                source="meta",
                content=content,
                posted_at=_ts(item.get("timestamp")),
                metadata={
                    "platform": "facebook",
                    "source_type": "facebook_comment",
                    "via": "zip_export",
                    "context": _fix_encoding(item.get("title") or ""),
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    def _load_json(self, z: zipfile.ZipFile, fname: str) -> dict | list:
        try:
            with z.open(fname) as f:
                return json.loads(f.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            print(f"[FacebookZipParser] could not read {fname}: {exc}")
            return []
