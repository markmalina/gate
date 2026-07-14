"""Process-scoped SQLAlchemy runtime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    """One engine and session factory owned by one application lifespan."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


def build_database_runtime(database_url: str) -> DatabaseRuntime:
    """Build the process-scoped database runtime without opening a connection."""

    normalized_url = database_url.strip()
    if not normalized_url:
        raise RuntimeError("DATABASE_URL is required")

    engine = create_async_engine(normalized_url, pool_pre_ping=True)
    return DatabaseRuntime(
        engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
    )


@asynccontextmanager
async def database_runtime_lifespan(database_url: str) -> AsyncIterator[DatabaseRuntime]:
    """Yield one runtime and always release its connection pool."""

    runtime = build_database_runtime(database_url)
    try:
        yield runtime
    finally:
        await runtime.engine.dispose()
