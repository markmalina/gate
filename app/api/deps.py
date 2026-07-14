"""FastAPI dependencies."""

from collections.abc import AsyncIterator

from app.infrastructure.database import DatabaseRuntime
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


def get_database_runtime(request: Request) -> DatabaseRuntime:
    """Return the runtime installed by the application lifespan."""

    runtime = getattr(request.app.state, "database_runtime", None)
    if not isinstance(runtime, DatabaseRuntime):
        raise RuntimeError("Database runtime is not initialized")
    return runtime


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session from the process-scoped factory."""

    runtime = get_database_runtime(request)
    async with runtime.session_factory() as session:
        yield session
