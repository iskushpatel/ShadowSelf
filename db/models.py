"""
ShadowSelf database models.

Tables created here:
  users            — app users (one row per person using ShadowSelf)
  raw_posts        — every ingested post/comment, normalized
  post_embeddings  — vector embeddings per post (pgvector)
  profile_snapshots — versioned shadow profiles over time

Design notes:
  - All PKs are UUIDs (consistent with PostCreate schema)
  - metadata stored as JSONB for flexibility (subreddit, score, etc.)
  - post_embeddings kept in a separate table so you can add/update
    embeddings without touching raw_posts
  - profile_snapshots are append-only — never update, always insert a new
    snapshot so you can show the user how their profile evolved over time
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # relationships
    posts: Mapped[list["RawPost"]] = relationship(back_populates="user", lazy="select")
    profile_snapshots: Mapped[list["ProfileSnapshot"]] = relationship(
        back_populates="user", lazy="select", order_by="ProfileSnapshot.created_at"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


# ── Raw Posts ─────────────────────────────────────────────────────────────────

class RawPost(Base):
    __tablename__ = "raw_posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # where the post came from
    source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "reddit_comment" | "reddit_post" | "manual" | "gmail" | "reddit_zip"

    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # sha256 hex — used for deduplication

    # original timestamp from source platform (nullable — manual posts won't have this)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # when we ingested it
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    # flexible platform-specific data (subreddit, score, url, etc.)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )

    # has this post been through the AI analysis pipeline yet?
    is_analyzed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # relationships
    user: Mapped["User"] = relationship(back_populates="posts")
    embedding: Mapped["PostEmbedding | None"] = relationship(
        back_populates="post", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        # fast lookup of all posts for a user
        Index("ix_raw_posts_user_id", "user_id"),
        # fast lookup by source type
        Index("ix_raw_posts_user_source", "user_id", "source"),
        # deduplication — same user can't have two posts with same hash
        UniqueConstraint("user_id", "content_hash", name="uq_raw_posts_user_hash"),
    )

    def __repr__(self) -> str:
        return f"<RawPost id={self.id} source={self.source} user={self.user_id}>"


# ── Post Embeddings ───────────────────────────────────────────────────────────

class PostEmbedding(Base):
    """
    Stores the vector embedding for each post.
    Kept separate from RawPost so you can regenerate embeddings
    without touching the raw content table.

    embedding: 1536-dim vector from text-embedding-3-small
    """

    __tablename__ = "post_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_posts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # one embedding per post
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Stored as JSONB for deployability. The current MVP does not query vectors,
    # and many hosted Postgres plans do not enable pgvector by default.
    embedding: Mapped[list[float]] = mapped_column(JSONB, nullable=False)

    model: Mapped[str] = mapped_column(
        String(100), default="text-embedding-3-small"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    # relationship
    post: Mapped["RawPost"] = relationship(back_populates="embedding")

    __table_args__ = (
        Index("ix_post_embeddings_user_id", "user_id"),
        # pgvector HNSW index for fast approximate nearest-neighbour search
        # created separately in vector_store.py after table creation
    )

    def __repr__(self) -> str:
        return f"<PostEmbedding post_id={self.post_id}>"


# ── Profile Snapshots ─────────────────────────────────────────────────────────

class ProfileSnapshot(Base):
    """
    A point-in-time shadow profile for a user.
    Never updated — always insert a new row so history is preserved.

    OCEAN scores are 0.0-1.0.
    confidence scores reflect how certain the model is given available data.
    summary is the natural language profile paragraph.
    topics is a JSONB list of {theme, weight, example_posts[]}.
    raw_output is the full agent response for debugging/re-processing.
    """

    __tablename__ = "profile_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # OCEAN scores
    openness: Mapped[float | None] = mapped_column(Float, nullable=True)
    conscientiousness: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraversion: Mapped[float | None] = mapped_column(Float, nullable=True)
    agreeableness: Mapped[float | None] = mapped_column(Float, nullable=True)
    neuroticism: Mapped[float | None] = mapped_column(Float, nullable=True)

    # per-trait confidence (0.0–1.0)
    ocean_confidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # e.g. {"openness": 0.85, "conscientiousness": 0.72, ...}

    # interest/topic clusters
    topics: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # e.g. [{"theme": "career anxiety", "weight": 0.4, "posts": [...]}, ...]

    # dominant emotions seen across posts
    sentiment_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # e.g. {"dominant": "curious", "valence": 0.6, "arc": [...]}

    # natural language 2–3 paragraph profile
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # how many posts were used to build this snapshot
    posts_analyzed: Mapped[int] = mapped_column(Integer, default=0)

    # full agent output stored for debugging/re-processing
    raw_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    # relationship
    user: Mapped["User"] = relationship(back_populates="profile_snapshots")

    __table_args__ = (
        Index("ix_profile_snapshots_user_id", "user_id"),
        Index("ix_profile_snapshots_created_at", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ProfileSnapshot user={self.user_id} posts={self.posts_analyzed}>"
