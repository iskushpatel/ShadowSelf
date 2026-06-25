from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.orchestrator import Orchestrator
from backend.ingestion.base_parser import BaseParser
from backend.ingestion.connected_clients import (
    GmailApiClient,
    LinkedInApiClient,
    MetaGraphApiClient,
)
from backend.ingestion.linkedin_export_parser import LinkedInExportParser
from backend.ingestion.instagram_zip_parser import InstagramZipParser
from backend.ingestion.tavily_client import TavilyClient
import tempfile
import os
from backend.ingestion.github_client import GithubPublicClient
from backend.ingestion.manual_parser import ManualParser
from backend.ingestion.reddit_client import RedditLiveClient
from backend.ingestion.reddit_parser import RedditZipParser
from backend.schemas.post import PostCreate
from db.models import ProfileSnapshot, RawPost, User


class EmptyContentError(ValueError):
    """Raised when ingestion yields no meaningful posts."""


# Lightweight concrete parser to reuse BaseParser utilities (normalize/generate_hash/is_meaningful)
class _ParserUtil(BaseParser):
    async def parse(self, source, user_id):
        # no-op async generator
        if False:
            yield

_PARSER_UTIL = _ParserUtil()


async def resolve_user(
    db: AsyncSession,
    *,
    user_id: UUID | None = None,
    email: str | None = None,
    username: str | None = None,
) -> User:
    user: User | None = None

    if user_id:
        user = await db.get(User, user_id)
        if user:
            return user

    # Use .first() instead of scalar_one_or_none() to handle duplicate rows
    # gracefully â€” duplicates can exist if the same username was created
    # multiple times before a unique constraint was added.
    if email:
        result = await db.execute(
            select(User).where(User.email == email).limit(1)
        )
        user = result.scalars().first()

    if user is None and username:
        result = await db.execute(
            select(User).where(User.username == username).limit(1)
        )
        user = result.scalars().first()

    if user is None:
        user = User(email=email or None, username=username or None)
        db.add(user)
        await db.flush()

    return user


_SOURCE_ALIASES: dict[str, list[str]] = {
    "reddit_live": ["reddit_comment", "reddit_post"],
    "reddit_zip":  ["reddit_comment", "reddit_post"],
}


async def _persist_posts(
    db: AsyncSession,
    *,
    user_id: UUID,
    source: str,
    posts: AsyncGenerator[PostCreate, None],
) -> tuple[int, int]:
    ingested_count = 0
    deduped_count = 0
    seen_hashes: set[str] = set()

    async for post in posts:
        ingested_count += 1

        # Always ensure content_hash is set â€” generate if missing
        if not post.content_hash:
            post.content_hash = _PARSER_UTIL.generate_hash(post.content)

        if post.content_hash in seen_hashes:
            deduped_count += 1
            continue

        # Check DB for duplicate hash
        try:
            exists = await db.execute(
                select(RawPost.id).where(
                    RawPost.user_id == user_id,
                    RawPost.content_hash == post.content_hash,
                )
            )
            if exists.scalar_one_or_none():
                deduped_count += 1
                seen_hashes.add(post.content_hash)
                continue
        except Exception as exc:
            print(f"[_persist_posts] DB lookup error (skipping post): {exc}")
            continue

        seen_hashes.add(post.content_hash)

        try:
            stmt = (
                insert(RawPost)
                .values(
                    user_id=user_id,
                    source=post.source or source,
                    content=post.content,
                    content_hash=post.content_hash,
                    posted_at=post.posted_at,
                    metadata_=post.metadata,
                )
                .on_conflict_do_nothing(constraint="uq_raw_posts_user_hash")
            )
            result = await db.execute(stmt)
            if result.rowcount == 0:
                deduped_count += 1
        except Exception as exc:
            print(f"[_persist_posts] DB insert error (skipping post): {exc}")
            await db.rollback()
            continue

    # Only error if parser returned NOTHING and user has no existing data
    if ingested_count == 0:
        source_values = _SOURCE_ALIASES.get(source, [source])
        try:
            existing = await db.execute(
                select(RawPost.id)
                .where(
                    RawPost.user_id == user_id,
                    RawPost.source.in_(source_values),
                )
                .limit(1)
            )
            if existing.scalar_one_or_none() is None:
                raise EmptyContentError(
                    f"No posts returned for source '{source}'. "
                    "The account may be private, suspended, or have no public history."
                )
        except EmptyContentError:
            raise
        except Exception:
            pass  # DB error during check â€” don't mask the original issue

    await db.flush()
    return ingested_count, deduped_count


async def ingest_manual(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    text: str,
    source_name: str,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    parser = ManualParser()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="manual",
        posts=parser.parse(text, user.id, source_name=source_name),
    )
    return user, *counts


async def ingest_reddit_live(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    reddit_username: str,
) -> tuple[User, int, int]:
    user = await resolve_user(
        db,
        user_id=user_id,
        email=email,
        username=username or reddit_username,
    )
    parser = RedditLiveClient()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="reddit_live",
        posts=parser.parse(reddit_username, user.id),
    )
    return user, *counts


async def ingest_reddit_zip(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    zip_path: str,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    parser = RedditZipParser()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="reddit_zip",
        posts=parser.parse(zip_path, user.id),
    )
    return user, *counts


async def ingest_github(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    github_username: str,
) -> tuple[User, int, int]:
    user = await resolve_user(
        db,
        user_id=user_id,
        email=email,
        username=username or github_username,
    )
    parser = GithubPublicClient()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="github",
        posts=parser.parse(github_username, user.id),
    )
    return user, *counts


async def ingest_gmail(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    access_token: str,
    max_results: int,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    parser = GmailApiClient()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="gmail",
        posts=parser.parse(access_token, user.id, max_results=max_results),
    )
    return user, *counts


async def ingest_linkedin(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    access_token: str,
    max_results: int,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    parser = LinkedInApiClient()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="linkedin",
        posts=parser.parse(access_token, user.id, max_results=max_results),
    )
    return user, *counts


async def ingest_meta(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    access_token: str,
    max_results: int,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    parser = MetaGraphApiClient()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="meta",
        posts=parser.parse(access_token, user.id, max_results=max_results),
    )
    return user, *counts


async def ingest_linkedin_upload(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    zip_bytes: bytes,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    parser = LinkedInExportParser()
    counts = await _persist_posts(
        db,
        user_id=user.id,
        source="linkedin",
        posts=parser.parse(zip_bytes, user.id),
    )
    return user, *counts


async def ingest_meta_upload(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    zip_bytes: bytes,
) -> tuple[User, int, int]:
    # Instagram parser expects a path; write to temp file
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    tf = None
    try:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tf.write(zip_bytes)
        tf.flush()
        tf.close()
        parser = InstagramZipParser()
        counts = await _persist_posts(
            db,
            user_id=user.id,
            source="meta",
            posts=parser.parse(tf.name, user.id),
        )
    finally:
        if tf is not None:
            try:
                os.unlink(tf.name)
            except Exception:
                pass
    return user, *counts


async def ingest_linkedin_search(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    email: str | None,
    username: str | None,
    query: str | None = None,
    profile_url: str | None = None,
    max_results: int = 5,
) -> tuple[User, int, int]:
    user = await resolve_user(db, user_id=user_id, email=email, username=username or query or profile_url)
    client = TavilyClient()

    # If a direct profile URL was provided, prioritize it and skip search
    urls = []
    if profile_url:
        urls = [profile_url]
    elif query:
        try:
            results = await client.search_profiles(query, max_results=max_results)
            urls = [r.get("url") for r in results if r.get("url")]
        except Exception as exc:
            print(f"[tavily] search failed for '{query}': {exc}")
            urls = []

    async def gen():
        base = _PARSER_UTIL
        yielded = False
        for url in urls:
            if not url or "linkedin.com" not in url.lower():
                continue
            try:
                # LinkedIn often blocks anonymous extraction; keep best effort here.
                paragraphs = await client.fetch_and_extract_linkedin(url, limit=10)
            except Exception as exc:
                print(f"[tavily] failed to fetch/extract {url}: {exc}")
                paragraphs = []
            for p in (paragraphs or []):
                content = base.normalize_text(p)
                if not base.is_meaningful(content):
                    continue
                post = PostCreate(
                    user_id=user.id,
                    source="linkedin_search",
                    content=content,
                    posted_at=None,
                    metadata={"platform": "linkedin", "source_type": "linkedin_public", "url": url},
                )
                post.content_hash = base.generate_hash(post.content)
                yielded = True
                yield post

        if not yielded and (profile_url or query):
            descriptor = profile_url or query or "LinkedIn profile"
            content = base.normalize_text(
                "LinkedIn public profile reference. "
                f"Input: {descriptor}. "
                "The public page could not be extracted reliably, which is common for LinkedIn. "
                "Use LinkedIn OAuth, a LinkedIn data export, or paste profile/about/activity text manually for richer analysis."
            )
            post = PostCreate(
                user_id=user.id,
                source="linkedin_search",
                content=content,
                posted_at=None,
                metadata={
                    "platform": "linkedin",
                    "source_type": "linkedin_public_reference",
                    "url": profile_url,
                    "query": query,
                    "fallback": True,
                },
            )
            post.content_hash = base.generate_hash(post.content)
            yield post

    counts = await _persist_posts(db, user_id=user.id, source="linkedin_search", posts=gen())
    return user, *counts


async def build_profile_snapshot(
    db: AsyncSession,
    *,
    user_id: UUID,
    orchestrator: Orchestrator | None = None,
) -> ProfileSnapshot:
    result = await db.execute(
        select(RawPost)
        .where(RawPost.user_id == user_id)
        .order_by(RawPost.posted_at.asc().nullslast(), RawPost.ingested_at.asc())
    )
    posts = list(result.scalars().all())
    if not posts:
        raise EmptyContentError("No posts available for analysis.")

    orchestrator = orchestrator or Orchestrator()
    profile = await orchestrator.build_profile(
        [post.content for post in posts],
        user_id=user_id,
    )

    snapshot = ProfileSnapshot(
        user_id=user_id,
        openness=profile["ocean"].get("openness"),
        conscientiousness=profile["ocean"].get("conscientiousness"),
        extraversion=profile["ocean"].get("extraversion"),
        agreeableness=profile["ocean"].get("agreeableness"),
        neuroticism=profile["ocean"].get("neuroticism"),
        ocean_confidence=profile.get("ocean_confidence"),
        topics=profile.get("topics"),
        sentiment_summary=profile.get("sentiment_summary"),
        summary=profile.get("summary"),
        posts_analyzed=len(posts),
        # Store all new fields inside raw_output â€” no DB migration needed
        raw_output={
            **(profile.get("raw_output") or {}),
            "headline": profile.get("headline"),
            "perceived_as": profile.get("perceived_as"),
            "archetype": profile.get("archetype"),
            "ocean_readable": profile.get("ocean_readable"),
            "strengths": profile.get("strengths"),
            "blind_spots": profile.get("blind_spots"),
            "communication_style": profile.get("communication_style"),
            "emotional_signature": profile.get("emotional_signature"),
            "how_to_connect": profile.get("how_to_connect"),
        },
    )
    db.add(snapshot)

    for post in posts:
        post.is_analyzed = True

    await db.flush()
    return snapshot


async def get_latest_snapshot(db: AsyncSession, *, user_id: UUID) -> ProfileSnapshot | None:
    result = await db.execute(
        select(ProfileSnapshot)
        .where(ProfileSnapshot.user_id == user_id)
        .order_by(ProfileSnapshot.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_snapshot_history(db: AsyncSession, *, user_id: UUID) -> Sequence[ProfileSnapshot]:
    result = await db.execute(
        select(ProfileSnapshot)
        .where(ProfileSnapshot.user_id == user_id)
        .order_by(ProfileSnapshot.created_at.desc())
    )
    return list(result.scalars().all())


def snapshot_to_response(snapshot: ProfileSnapshot) -> dict[str, Any]:
    raw = snapshot.raw_output or {}
    return {
        "snapshot_id": snapshot.id,
        "user_id": snapshot.user_id,
        # â”€â”€ Core narrative (pulled from raw_output, no migration needed) â”€â”€
        "headline": raw.get("headline"),
        "perceived_as": raw.get("perceived_as"),
        "archetype": raw.get("archetype"),
        "summary": snapshot.summary,
        # â”€â”€ OCEAN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "ocean": {
            "openness": snapshot.openness,
            "conscientiousness": snapshot.conscientiousness,
            "extraversion": snapshot.extraversion,
            "agreeableness": snapshot.agreeableness,
            "neuroticism": snapshot.neuroticism,
        },
        "ocean_confidence": snapshot.ocean_confidence,
        "ocean_readable": raw.get("ocean_readable"),
        # â”€â”€ Insights â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "strengths": raw.get("strengths"),
        "blind_spots": raw.get("blind_spots"),
        "communication_style": raw.get("communication_style"),
        "emotional_signature": raw.get("emotional_signature"),
        "how_to_connect": raw.get("how_to_connect"),
        # â”€â”€ Topics + sentiment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "topics": snapshot.topics,
        "sentiment_summary": snapshot.sentiment_summary,
        # â”€â”€ Meta â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "posts_analyzed": snapshot.posts_analyzed,
        "created_at": snapshot.created_at,
        "raw_output": raw,
    }

async def ingest_facebook_upload(
    db,
    *,
    user_id,
    email,
    username,
    zip_bytes: bytes,
):
    import tempfile, os
    from backend.ingestion.facebook_zip_parser import FacebookZipParser
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    tf = None
    try:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tf.write(zip_bytes); tf.flush(); tf.close()
        parser = FacebookZipParser()
        counts = await _persist_posts(db, user_id=user.id, source="meta", posts=parser.parse(tf.name, user.id))
    finally:
        if tf:
            try: os.unlink(tf.name)
            except Exception: pass
    return user, *counts


async def ingest_instagram_upload(
    db,
    *,
    user_id,
    email,
    username,
    zip_bytes: bytes,
):
    import tempfile, os
    from backend.ingestion.instagram_zip_parser import InstagramZipParser
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    tf = None
    try:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tf.write(zip_bytes); tf.flush(); tf.close()
        parser = InstagramZipParser()
        counts = await _persist_posts(db, user_id=user.id, source="meta", posts=parser.parse(tf.name, user.id))
    finally:
        if tf:
            try: os.unlink(tf.name)
            except Exception: pass
    return user, *counts

