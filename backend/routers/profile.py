from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.api import ProfileHistoryItem, ProfileSnapshotResponse
from backend.services import get_latest_snapshot, get_snapshot_history, snapshot_to_response
from db.session import get_db


router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/{user_id}", response_model=ProfileSnapshotResponse)
async def get_profile(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ProfileSnapshotResponse:
    snapshot = await get_latest_snapshot(db, user_id=user_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return ProfileSnapshotResponse(**snapshot_to_response(snapshot))


@router.get("/{user_id}/history", response_model=list[ProfileHistoryItem])
async def get_profile_history(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ProfileHistoryItem]:
    history = await get_snapshot_history(db, user_id=user_id)
    return [
        ProfileHistoryItem(
            snapshot_id=item.id,
            created_at=item.created_at,
            summary=item.summary,
            posts_analyzed=item.posts_analyzed,
        )
        for item in history
    ]
