"""SQLAlchemy 2.0 async engine and session factory.

Per CLAUDE.md:
- SQLAlchemy 2.0 async with asyncpg driver
- async/await for all I/O
- Every query filters by tenant_id via TenantContext
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import settings

_use_supabase = bool(settings.SUPABASE_DATABASE_URL)

_engine_kwargs: dict = {
    "echo": False,  # Disable SQL echo — Windows cp1252 crashes on ₹ symbols in query params
}
if settings.ENVIRONMENT == "test":
    _engine_kwargs["poolclass"] = NullPool
elif _use_supabase:
    # Supabase Transaction pooler (port 6543) — pgbouncer does the pooling.
    # NullPool: let pgbouncer manage connections; SQLAlchemy pool on top causes orphan buildup.
    # statement_cache_size=0 + prepared_statement_cache_size=0: pgbouncer Transaction mode
    # rotates backends per statement, so asyncpg can't cache prepared statements.
    import ssl as _ssl
    _ssl_ctx = _ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs["connect_args"] = {
        "ssl": _ssl_ctx,
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "server_settings": {"application_name": "snowkap-web"},
    }
    # Disable SQLAlchemy's prepared statement naming to avoid pgbouncer conflicts
    _engine_kwargs["pool_pre_ping"] = False
else:
    _engine_kwargs["pool_size"] = 20
    _engine_kwargs["max_overflow"] = 10

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

# pgbouncer Transaction mode workaround: disable asyncpg's internal prepared
# statement cache on each new connection via SQLAlchemy event hook.
if _use_supabase:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_asyncpg_no_cache(dbapi_connection, connection_record):
        # dbapi_connection is the raw asyncpg AdaptedConnection wrapper
        raw = getattr(dbapi_connection, "_connection", None)
        if raw is not None:
            raw._stmt_cache.clear()

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
    _worker_kwargs: dict = {"echo": settings.DEBUG, "poolclass": NullPool}
    if _use_supabase:
        import ssl as _ssl2
        _ssl_ctx2 = _ssl2.create_default_context()
        _ssl_ctx2.check_hostname = False
        _ssl_ctx2.verify_mode = _ssl2.CERT_NONE
        _worker_kwargs["connect_args"] = {
            "ssl": _ssl_ctx2,
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"application_name": "snowkap-celery"},
        }
    worker_engine = create_async_engine(settings.DATABASE_URL, **_worker_kwargs)
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
