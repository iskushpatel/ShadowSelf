from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class IngestManualRequest(BaseModel):
    user_id: UUID | None = None
    email: str | None = None
    username: str | None = None
    text: str = Field(min_length=1)
    source_name: str = "manual_paste"


class IngestRedditLiveRequest(BaseModel):
    user_id: UUID | None = None
    email: str | None = None
    username: str | None = None
    reddit_username: str = Field(min_length=1)


class IngestRedditZipRequest(BaseModel):
    user_id: UUID | None = None
    email: str | None = None
    username: str | None = None
    zip_path: str = Field(min_length=1)


class IngestGithubRequest(BaseModel):
    user_id: UUID | None = None
    email: str | None = None
    username: str | None = None
    github_username: str = Field(min_length=1)


class IngestTokenSourceRequest(BaseModel):
    user_id: UUID | None = None
    email: str | None = None
    username: str | None = None
    access_token: str = Field(min_length=1)
    max_results: int = Field(default=25, ge=1, le=100)


class IngestResult(BaseModel):
    user_id: UUID
    source: str
    ingested_count: int
    deduped_count: int


class AnalyzeResult(BaseModel):
    snapshot_id: UUID
    user_id: UUID
    posts_analyzed: int
    created_at: datetime


class ProfileSnapshotResponse(BaseModel):
    snapshot_id: UUID
    user_id: UUID
    posts_analyzed: int
    created_at: datetime

    # ── Core narrative ────────────────────────────────────────────────────
    headline: str | None = None          # "The Curious Builder Who Thinks Out Loud"
    perceived_as: str | None = None      # How others see this person online
    summary: str | None = None           # 4-6 sentence narrative profile
    archetype: str | None = None         # "The Analyst", "The Storyteller", etc.

    # ── OCEAN scores ──────────────────────────────────────────────────────
    ocean: dict[str, float | None] = Field(default_factory=dict)
    ocean_confidence: dict[str, float] | None = None
    ocean_readable: dict[str, str] | None = None   # Human explanation per trait

    # ── Strengths & blind spots ───────────────────────────────────────────
    strengths: list[str] | None = None
    blind_spots: list[str] | None = None

    # ── Communication & emotion ───────────────────────────────────────────
    communication_style: str | None = None
    emotional_signature: str | None = None
    how_to_connect: str | None = None

    # ── Topics ────────────────────────────────────────────────────────────
    topics: list[dict[str, Any]] | None = None     # now includes "insight" field

    # ── Sentiment ─────────────────────────────────────────────────────────
    sentiment_summary: dict[str, Any] | None = None

    # ── Raw agent outputs (debug) ─────────────────────────────────────────
    raw_output: dict[str, Any] | None = None


class ProfileHistoryItem(BaseModel):
    snapshot_id: UUID
    created_at: datetime
    summary: str | None
    posts_analyzed: int


class HealthResponse(BaseModel):
    status: str
    database: str
