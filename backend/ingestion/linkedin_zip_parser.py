"""
LinkedIn ZIP export parser.

How to get: LinkedIn → Settings → Data Privacy → Get a copy of your data
            Select "Want something in particular?" → Shares, Comments, Messages, Profile
            Download arrives within 10 minutes as a ZIP.

Key files inside the ZIP:
  - Shares.csv        → your posts (ShareCommentary, Date, ShareLink, ShareMediaCategory)
  - Comments.csv      → your comments (Message, Date, Link, ParentAuthor)
  - messages.csv      → DMs (optional signal)
  - Profile.csv       → bio / headline
  - Connections.csv   → not parsed (no text content)
"""

import csv
import io
import zipfile
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID

from .base_parser import BaseParser
from ..schemas.post import PostCreate

_SKIP = {"", "nan", "none", "[removed]", "[deleted]"}


def _clean(v: str | None) -> str:
    return (v or "").strip()


def _parse_date(v: str) -> datetime | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class LinkedInZipParser(BaseParser):
    """Parses LinkedIn's GDPR data export ZIP."""

    async def parse(self, zip_path: str, user_id: UUID) -> AsyncGenerator[PostCreate, None]:
        count = 0
        with zipfile.ZipFile(zip_path) as z:
            names = [n.lower() for n in z.namelist()]
            print(f"[LinkedInZipParser] files in ZIP: {z.namelist()}")

            for zname in z.namelist():
                lower = zname.lower()

                if "shares" in lower and lower.endswith(".csv"):
                    async for p in self._parse_shares(z, zname, user_id):
                        yield p; count += 1

                elif "comments" in lower and lower.endswith(".csv"):
                    async for p in self._parse_comments(z, zname, user_id):
                        yield p; count += 1

                elif "profile" in lower and lower.endswith(".csv"):
                    async for p in self._parse_profile(z, zname, user_id):
                        yield p; count += 1

        print(f"[LinkedInZipParser] done — {count} items")

    async def _parse_shares(self, z, fname, user_id):
        rows = self._read_csv(z, fname)
        for row in rows:
            text = _clean(row.get("ShareCommentary") or row.get("Commentary") or "")
            if not text or text.lower() in _SKIP:
                continue
            content = self.normalize_text(text)
            if not self.is_meaningful(content):
                continue
            date_str = _clean(row.get("Date") or row.get("Timestamp") or "")
            post = PostCreate(
                user_id=user_id,
                source="linkedin",
                content=content,
                posted_at=_parse_date(date_str),
                metadata={
                    "platform": "linkedin",
                    "source_type": "linkedin_post",
                    "via": "zip_export",
                    "url": _clean(row.get("ShareLink") or row.get("URL") or ""),
                    "media_category": _clean(row.get("ShareMediaCategory") or ""),
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    async def _parse_comments(self, z, fname, user_id):
        rows = self._read_csv(z, fname)
        for row in rows:
            text = _clean(row.get("Message") or row.get("Comment") or "")
            if not text or text.lower() in _SKIP:
                continue
            content = self.normalize_text(text)
            if not self.is_meaningful(content):
                continue
            date_str = _clean(row.get("Date") or row.get("Timestamp") or "")
            post = PostCreate(
                user_id=user_id,
                source="linkedin",
                content=content,
                posted_at=_parse_date(date_str),
                metadata={
                    "platform": "linkedin",
                    "source_type": "linkedin_comment",
                    "via": "zip_export",
                    "parent_author": _clean(row.get("ParentAuthor") or ""),
                    "url": _clean(row.get("Link") or row.get("URL") or ""),
                },
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    async def _parse_profile(self, z, fname, user_id):
        rows = self._read_csv(z, fname)
        for row in rows:
            parts = []
            for field in ("Headline", "Summary", "About", "Industry", "Position"):
                v = _clean(row.get(field) or "")
                if v and v.lower() not in _SKIP:
                    parts.append(v)
            if not parts:
                continue
            content = self.normalize_text(" | ".join(parts))
            if not self.is_meaningful(content):
                continue
            post = PostCreate(
                user_id=user_id,
                source="linkedin",
                content=content,
                posted_at=None,
                metadata={"platform": "linkedin", "source_type": "linkedin_profile", "via": "zip_export"},
            )
            post.content_hash = self.generate_hash(post.content)
            yield post

    def _read_csv(self, z: zipfile.ZipFile, fname: str) -> list[dict]:
        try:
            with z.open(fname) as f:
                text = f.read().decode("utf-8-sig", errors="replace")
                return list(csv.DictReader(io.StringIO(text)))
        except Exception as exc:
            print(f"[LinkedInZipParser] could not read {fname}: {exc}")
            return []
