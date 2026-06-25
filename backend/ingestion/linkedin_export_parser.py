"""
LinkedIn data export parser.

How to get your export:
  LinkedIn → Settings → Data Privacy → Get a copy of your data
  → Select "Posts, Articles & Activity" (or "All data") → Request archive
  → Download ZIP when email arrives (~10 minutes)

Files we parse inside the ZIP:
  Shares.csv          — your text posts and reshares
  Comments.csv        — your comments on others' posts
  messages.csv        — direct messages you sent (optional signal)
  Profile.csv         — headline, summary, about section

Column names vary slightly between export versions; we try multiple variants.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from uuid import UUID

from .base_parser import BaseParser
from ..schemas.post import PostCreate

# Rows whose content matches these strings are noise
_SKIP = {"", "nan", "none", "n/a", "[deleted]", "[removed]"}

# LinkedIn sometimes exports dates as "2023-06-15 10:23:44 UTC" or ISO
_DATE_FMTS = [
    "%Y-%m-%d %H:%M:%S UTC",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d",
]


def _parse_date(val: str) -> datetime | None:
    if not val or val.strip().lower() in _SKIP:
        return None
    val = val.strip()
    for fmt in _DATE_FMTS:
        try:
            dt = datetime.strptime(val, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


def _clean(val: str | None) -> str:
    return (val or "").strip().strip('"').strip()


class LinkedInExportParser(BaseParser):
    """Parse a LinkedIn GDPR data export ZIP into PostCreate objects."""

    async def parse(
        self,
        zip_bytes: bytes,
        user_id: UUID,
    ) -> AsyncGenerator[PostCreate, None]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid ZIP file: {exc}") from exc

        names_lower = {n.lower(): n for n in zf.namelist()}
        parsed = 0

        # ── Shares (your posts) ───────────────────────────────────────────
        for candidate in ("shares.csv", "posts/shares.csv"):
            if candidate in names_lower:
                with zf.open(names_lower[candidate]) as f:
                    rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
                for row in rows:
                    post = self._share_row(row, user_id)
                    if post:
                        yield post
                        parsed += 1
                break

        # ── Comments ──────────────────────────────────────────────────────
        for candidate in ("comments.csv", "posts/comments.csv"):
            if candidate in names_lower:
                with zf.open(names_lower[candidate]) as f:
                    rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
                for row in rows:
                    post = self._comment_row(row, user_id)
                    if post:
                        yield post
                        parsed += 1
                break

        # ── Profile summary ───────────────────────────────────────────────
        for candidate in ("profile.csv", "basic information/profile.csv"):
            if candidate in names_lower:
                with zf.open(names_lower[candidate]) as f:
                    rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
                for row in rows:
                    post = self._profile_row(row, user_id)
                    if post:
                        yield post
                        parsed += 1
                break

        # ── Messages (sent only) ──────────────────────────────────────────
        for candidate in ("messages.csv", "messages/messages.csv"):
            if candidate in names_lower:
                with zf.open(names_lower[candidate]) as f:
                    rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
                for row in rows:
                    # Only ingest messages we SENT
                    direction = _clean(row.get("DIRECTION", row.get("Direction", ""))).lower()
                    if direction and "sent" not in direction:
                        continue
                    post = self._message_row(row, user_id)
                    if post:
                        yield post
                        parsed += 1
                break

        print(f"[LinkedInExportParser] yielded {parsed} posts from ZIP")

    # ── row converters ────────────────────────────────────────────────────

    def _share_row(self, row: dict, user_id: UUID) -> PostCreate | None:
        # Column names seen across different export versions
        text = _clean(
            row.get("ShareCommentary")
            or row.get("Commentary")
            or row.get("Text")
            or row.get("Content")
            or ""
        )
        if not text or text.lower() in _SKIP:
            return None

        # If it's just a reshare with no commentary, skip — low signal
        media_type = _clean(row.get("ShareMediaCategory", row.get("MediaCategory", ""))).lower()
        if not text and media_type in ("none", ""):
            return None

        date_val = _clean(row.get("Date") or row.get("Timestamp") or "")
        content = self.normalize_text(text)
        if not self.is_meaningful(content):
            return None

        post = PostCreate(
            user_id=user_id,
            source="linkedin_post",
            content=content,
            posted_at=_parse_date(date_val),
            metadata={
                "platform": "linkedin",
                "source_type": "linkedin_post",
                "media_category": media_type,
                "url": _clean(row.get("URL") or row.get("ShareLink") or ""),
            },
        )
        post.content_hash = self.generate_hash(post.content)
        return post

    def _comment_row(self, row: dict, user_id: UUID) -> PostCreate | None:
        text = _clean(
            row.get("Message")
            or row.get("CommentMessage")
            or row.get("Comment")
            or row.get("Text")
            or ""
        )
        if not text or text.lower() in _SKIP:
            return None

        date_val = _clean(row.get("Date") or row.get("Timestamp") or "")
        content = self.normalize_text(text)
        if not self.is_meaningful(content):
            return None

        post = PostCreate(
            user_id=user_id,
            source="linkedin_comment",
            content=content,
            posted_at=_parse_date(date_val),
            metadata={
                "platform": "linkedin",
                "source_type": "linkedin_comment",
                "link": _clean(row.get("Link") or row.get("PostLink") or ""),
            },
        )
        post.content_hash = self.generate_hash(post.content)
        return post

    def _profile_row(self, row: dict, user_id: UUID) -> PostCreate | None:
        # Profile CSV has one row per field; we want Summary / About
        first_name = _clean(row.get("First Name", ""))
        last_name  = _clean(row.get("Last Name", ""))
        headline   = _clean(row.get("Headline", ""))
        summary    = _clean(row.get("Summary", row.get("About", "")))

        parts = []
        if first_name or last_name:
            parts.append(f"LinkedIn profile: {first_name} {last_name}".strip())
        if headline:
            parts.append(f"Headline: {headline}")
        if summary:
            parts.append(f"Summary: {summary}")

        text = "\n".join(p for p in parts if p)
        if not text:
            return None

        content = self.normalize_text(text)
        if not self.is_meaningful(content):
            return None

        post = PostCreate(
            user_id=user_id,
            source="linkedin_profile",
            content=content,
            posted_at=None,
            metadata={"platform": "linkedin", "source_type": "linkedin_profile"},
        )
        post.content_hash = self.generate_hash(post.content)
        return post

    def _message_row(self, row: dict, user_id: UUID) -> PostCreate | None:
        text = _clean(
            row.get("CONTENT")
            or row.get("Content")
            or row.get("MESSAGE")
            or row.get("Message")
            or ""
        )
        if not text or text.lower() in _SKIP:
            return None

        date_val = _clean(row.get("DATE") or row.get("Date") or row.get("TIMESTAMP") or "")
        content = self.normalize_text(text)
        if not self.is_meaningful(content):
            return None

        post = PostCreate(
            user_id=user_id,
            source="linkedin_message",
            content=content,
            posted_at=_parse_date(date_val),
            metadata={
                "platform": "linkedin",
                "source_type": "linkedin_message",
                "conversation": _clean(row.get("CONVERSATION TITLE") or row.get("ConversationTitle") or ""),
            },
        )
        post.content_hash = self.generate_hash(post.content)
        return post
