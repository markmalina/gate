"""Process-scoped SQLAlchemy runtime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime

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
    sequences: "ReplySequenceRuntime | None" = None


def build_database_runtime(database_url: str) -> DatabaseRuntime:
    """Build the process-scoped database runtime without opening a connection."""

    normalized_url = database_url.strip()
    if not normalized_url:
        raise RuntimeError("DATABASE_URL is required")

    engine = create_async_engine(normalized_url, pool_pre_ping=True)
    from app.core.config import get_settings

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sequences = attach_reply_sequence_runtime(engine, sessions, get_settings())
    return DatabaseRuntime(engine=engine, session_factory=sessions, sequences=sequences)


def attach_reply_sequence_runtime(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession], settings: "Settings"
) -> "ReplySequenceRuntime | None":
    from app.infrastructure.reply_sequence_runtime import RUNTIME_KEY, ReplySequenceRuntime

    sequences = None
    if (
        settings.gate_reply_sequence_dispatch_v2_enabled
        and not settings.gate_reply_sequence_stop_on_inbound_enabled
    ):
        raise ValueError("Sequence v2 requires the inbound stop guard")
    if (
        settings.gate_reply_sequence_stop_on_inbound_enabled
        and settings.gate_reply_sequence_worker_enabled
        and not settings.gate_reply_sequence_dispatch_v2_enabled
    ):
        raise ValueError("Legacy sequence worker cannot run with v2 guard enabled")
    if settings.gate_reply_sequence_stop_on_inbound_enabled:
        if settings.gate_reply_sequence_db_pool_mode not in {"direct", "session"}:
            raise ValueError("Sequence guard requires verified direct/session PostgreSQL pooling")
        sequences = ReplySequenceRuntime(engine=engine, settings=settings, sessions=sessions)
        sessions.configure(info={RUNTIME_KEY: sequences})
        sequences.register_transport_gate()
    return sequences


@asynccontextmanager
async def database_runtime_lifespan(database_url: str) -> AsyncIterator[DatabaseRuntime]:
    """Yield one runtime and always release its connection pool."""

    runtime = build_database_runtime(database_url)
    try:
        if runtime.sequences is not None:
            await runtime.sequences.invalidate_ingress("ingress_runtime_started")
        yield runtime
    finally:
        if runtime.sequences is not None:
            await runtime.sequences.close()
        await runtime.engine.dispose()
