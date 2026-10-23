"""
SQLAlchemy async database engine and session management.

Design decisions:
- AsyncSession throughout — no blocking DB calls in async context
- Session factory via dependency injection (FastAPI deps)
- Connection pooling configured for production load
- TimescaleDB-specific setup handled in Alembic migrations
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.shared.config.settings import get_settings
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger(__name__)

settings = get_settings()

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

engine = create_async_engine(
    str(settings.database.url),
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    pool_timeout=settings.database.pool_timeout,
    pool_pre_ping=True,  # Validate connections before use
    echo=settings.database.echo,
    echo_pool=False,
    future=True,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit (async-safe)
    autocommit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Base declarative model
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass


# ---------------------------------------------------------------------------
# Session providers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """
    Context manager that provides a database session.

    Handles commit/rollback automatically.
    Use this in application services and background tasks.

    Usage:
        async with get_db_session() as session:
            result = await session.execute(...)
    """
    session = AsyncSessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    FastAPI dependency for database sessions.

    Usage:
        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with get_db_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def create_tables() -> None:
    """Create all tables (for testing/dev without Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_tables_created")


async def dispose_engine() -> None:
    """Dispose the engine — call on application shutdown."""
    await engine.dispose()
    logger.info("database_engine_disposed")
