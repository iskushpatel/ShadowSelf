import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# ── engine ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://shadowself:shadowself_secret@localhost:5432/shadowself",
)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,       # set True to log all SQL during development
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # reconnect if connection dropped
)

# ── session factory ───────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # keep objects usable after commit
    autoflush=False,
    autocommit=False,
)

# ── declarative base (shared by all models) ───────────────────────────────────

class Base(DeclarativeBase):
    pass

# ── dependency for FastAPI ────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a DB session per request.

    Usage in a router:
        async def my_route(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

# ── create all tables (call on app startup) ───────────────────────────────────

async def create_tables() -> None:
    """
    Creates all tables defined in models.py.
    Call this from FastAPI's startup event.
    Safe to call multiple times — only creates tables that don't exist yet.
    """
    from . import models  # noqa: F401 — ensures models are registered on Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
