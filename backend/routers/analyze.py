from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.api import AnalyzeResult
from backend.agents.llm_json import MissingLLMConfigError
from backend.services import EmptyContentError, build_profile_snapshot
from db.session import get_db


router = APIRouter(prefix="/analyze", tags=["analyze"])


@router.post("/{user_id}", response_model=AnalyzeResult, status_code=status.HTTP_201_CREATED)
async def analyze_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> AnalyzeResult:
    try:
        snapshot = await build_profile_snapshot(db, user_id=user_id)
    except EmptyContentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MissingLLMConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {type(exc).__name__}: {exc}",
        ) from exc

    return AnalyzeResult(
        snapshot_id=snapshot.id,
        user_id=snapshot.user_id,
        posts_analyzed=snapshot.posts_analyzed,
        created_at=snapshot.created_at,
    )
