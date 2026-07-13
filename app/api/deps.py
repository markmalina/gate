"""FastAPI dependencies."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings


def get_database_url(settings: Settings | None = None) -> str:
    """Return the configured database URL or raise a deployment-safe error."""

    resolved = settings or get_settings()
    if not resolved.database_url:
        raise RuntimeError("DATABASE_URL is required")
    return resolved.database_url


def build_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Build an async SQLAlchemy session factory."""

    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session."""

    sessionmaker = build_sessionmaker(get_database_url())
    async with sessionmaker() as session:
        yield session
