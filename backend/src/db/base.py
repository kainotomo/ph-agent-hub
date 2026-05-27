# =============================================================================
# PH Agent Hub — SQLAlchemy 2.0 Declarative Base & Async Session Factory
# =============================================================================

from sqlalchemy import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
import uuid

from ..core.config import settings

# ---------------------------------------------------------------------------
# Async Engine
# ---------------------------------------------------------------------------
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

# ---------------------------------------------------------------------------
# Async Session Factory
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# FastAPI Dependency — yields an async DB session per request
# ---------------------------------------------------------------------------
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
