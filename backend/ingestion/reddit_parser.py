# backend/ingestion/reddit_parser.py
# Option A — ZIP export parser (user downloads their own data from Reddit settings)
# Use when: you want the user's FULL history including private/deleted posts
# How to get: Reddit → Settings → Data Request → Download ZIP (takes 24-48h)

import zipfile
import csv
import io
from datetime import datetime
from typing import AsyncGenerator
from uuid import UUID

from .base_parser import BaseParser
from ..schemas.post import PostCreate

# Content that is not worth analyzing
_SKIP_CONTENT = {"[removed]", "[deleted]", "", "nan"}


class RedditZipParser(BaseParser):
    """
    Parser for Reddit's official GDPR data export ZIP.

    Supported files inside the ZIP:
      - comments.csv      → user's comment history
      - posts.csv         → user's submitted posts
      - submitted.csv     → alias for posts on some exports
      - messages.csv      → direct messages (optional signal)

    Usage:
        parser = RedditZipParser()
        async for post in parser.parse("/path/to/reddit_export.zip", user_id):
            await db.save(post)
    """

    async def parse(self, zip_path: str, user_id: UUID) -> AsyncGenerator[PostCreate, None]:
        processed = 0

        with zipfile.ZipFile(zip_path) as z:
            csv_files = [n for n in z.namelist() if n.endswith(".csv")]

            for file_name in csv_files:
                source_type = self._detect_source_type(file_name)

                with z.open(file_name) as f:
                    reader = csv.DictReader(io.StringIO(f.read().decode("utf-8")))

                    for row in reader:
                        post = self._row_to_post(row, user_id, source_type)
                        if post:
                            yield post
                            processed += 1

        print(f"[RedditZipParser] yielded {processed} posts from {len(csv_files)} CSV files")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _detect_source_type(self, filename: str) -> str:
        name = filename.lower()
        if "comment" in name:
            return "reddit_comment"
        if "post" in name or "submitted" in name:
            return "reddit_post"
        if "chat" in name or "message" in name:
            return "reddit_message"
        return "reddit_export"

    def _row_to_post(self, row: dict, user_id: UUID, source_type: str) -> PostCreate | None:
        try:
            raw = self._extract_text(row, source_type)
            if not raw or raw.strip().lower() in _SKIP_CONTENT:
                return None

            content = self.normalize_text(raw)
            if not self.is_meaningful(content):
                return None

            return PostCreate(
                user_id=user_id,
                source="reddit_zip",
                content=content,
                created_at=self._parse_timestamp(row),
                content_hash=self.generate_hash(content),
                metadata=self._build_metadata(row, source_type),
            )
        except Exception:
            return None

    def _extract_text(self, row: dict, source_type: str) -> str:
        """Pull the main text out of a CSV row."""
        if source_type == "reddit_post":
            title = row.get("title", "").strip()
            body = row.get("selftext", row.get("body", "")).strip()
            if body and body.lower() not in _SKIP_CONTENT:
                return f"{title}\n\n{body}"
            return title

        for key in ("body", "comment_body", "selftext", "content", "message", "text"):
            val = row.get(key, "").strip()
            if val and val.lower() not in _SKIP_CONTENT:
                return val
        return ""

    def _parse_timestamp(self, row: dict) -> datetime | None:
        for key in ("created_at", "timestamp", "date", "created_utc", "time"):
            val = row.get(key, "").strip()
            if not val:
                continue
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                pass
            try:
                return datetime.fromtimestamp(float(val))
            except (ValueError, OSError):
                pass
        return None

    def _build_metadata(self, row: dict, source_type: str) -> dict:
        meta = {"platform": "reddit", "source_type": source_type}
        for field in ("id", "subreddit", "score", "permalink", "url", "num_comments", "flair"):
            if row.get(field):
                meta[field] = row[field]
        permalink = meta.get("permalink", "")
        if permalink and not permalink.startswith("http"):
            meta["url"] = f"https://reddit.com{permalink}"
        return meta
