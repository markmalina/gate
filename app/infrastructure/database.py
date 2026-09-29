"""Process-scoped SQLAlchemy runtime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.infrastructure.reply_canary_startup_fence import SqlCanaryStartupFence
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
    startup_fence: "tuple[str, SqlCanaryStartupFence] | None" = None


def build_database_runtime(
    database_url: str, *, settings: "Settings | None" = None
) -> DatabaseRuntime:
    """Build the process-scoped database runtime without opening a connection."""

    normalized_url = database_url.strip()
    if not normalized_url:
        raise RuntimeError("DATABASE_URL is required")

    from app.core.config import get_settings

    settings = settings if settings is not None else get_settings()
    validate_personal_bot_identity(settings)
    engine = create_async_engine(normalized_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sequences = attach_reply_sequence_runtime(engine, sessions, settings)
    fence = None
    if sequences is None:
        from app.infrastructure.reply_canary_startup_fence import SqlCanaryStartupFence
        from app.services.telegram_delivery_gate import register_gate, registered_gate

        prefix = settings.telegram_bot_token.split(":", 1)[0]
        if prefix.isdecimal() and int(prefix) > 0 and registered_gate(prefix) is None:
            gate = SqlCanaryStartupFence(sessions, settings.gate_workspace_id)
            register_gate(prefix, gate)
            fence = (prefix, gate)
    return DatabaseRuntime(
        engine=engine, session_factory=sessions, sequences=sequences, startup_fence=fence
    )


def validate_personal_bot_identity(settings: "Settings") -> None:
    """Other product webhook/raw transports must never impersonate the personal bot."""
    primary = settings.telegram_bot_token.strip().split(":", 1)[0]
    if not primary.isdecimal() or int(primary) <= 0:
        return
    for name in ("alpha", "beta", "become", "aqub"):
        configured = getattr(settings, f"telegram_{name}_bot_token", None)
        secondary = configured.get_secret_value().strip() if configured is not None else ""
        if secondary and secondary.split(":", 1)[0] == primary:
            raise ValueError("Personal Telegram bot identity must be isolated")


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
        await close_database_runtime(runtime)


async def close_database_runtime(runtime: DatabaseRuntime) -> None:
    """Release the same transport owner and engine for web and standalone workers."""
    try:
        if runtime.sequences is not None:
            await runtime.sequences.close()
    finally:
        if runtime.startup_fence is not None:
            from app.services.telegram_delivery_gate import unregister_gate

            unregister_gate(*runtime.startup_fence)
        await runtime.engine.dispose()
