"""SQLAlchemy 2.0 async engine and session factory.

Per CLAUDE.md:
- SQLAlchemy 2.0 async with asyncpg driver
- async/await for all I/O
- Every query filters by tenant_id via TenantContext
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import settings

_engine_kwargs: dict = {
    "echo": settings.DEBUG,
}
if settings.ENVIRONMENT == "test":
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs["pool_size"] = 20
    _engine_kwargs["max_overflow"] = 10

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def create_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create a fresh engine + session factory for Celery workers.

    Celery tasks run in separate processes/threads with their own event loops,
    so they need their own asyncpg connection pool (not shared with the API).
    """
    worker_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        poolclass=NullPool,
    )
    return async_sessionmaker(
        worker_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db() -> AsyncSession:
    """Dependency: yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
