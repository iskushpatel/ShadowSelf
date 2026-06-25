from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.api import HealthResponse
from db.session import get_db


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HealthResponse:
    startup_error = getattr(request.app.state, "startup_error", None)
    if startup_error:
        return HealthResponse(status="degraded", database=startup_error)

    await db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="connected")
