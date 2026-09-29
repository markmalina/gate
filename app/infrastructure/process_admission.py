"""Explicit schema-0111 observation to schema-0115 verified-epoch handoff.

The selected mode comes from the actual ledger, never an input claim. Baseline
proof remains strictly 0111. Candidate reads use the existing G2 durable health.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.baseline_observation import BASELINE_SCHEMA, run_baseline_observer
from app.infrastructure.database import DatabaseRuntime
from app.services.process_send_fence import (
    BaselineSendClosed,
    ProcessSendFence,
    install_process_send_fence,
    installed_process_send_fence,
)

CANDIDATE_SCHEMA = "0115_reply_canary_binding"


async def read_startup_schema(database: DatabaseRuntime) -> str:
    try:
        async with database.session_factory() as session:
            versions = (
                (await session.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
        if len(versions) != 1 or versions[0] not in {BASELINE_SCHEMA, CANDIDATE_SCHEMA}:
            raise ValueError("unsupported_schema")
        return str(versions[0])
    except Exception:
        raise BaselineSendClosed("process_schema_unavailable") from None


@asynccontextmanager
async def runtime_process_admission(
    database: DatabaseRuntime, *, settings: Settings, wait_baseline: bool = False
) -> AsyncIterator[None]:
    """One process owner; nested ASGI work borrows without replacing/closing it."""
    existing = installed_process_send_fence()
    if existing is not None and existing.observer_owned:
        if existing.runtime_owner is not database or existing.settings is not settings:
            raise BaselineSendClosed("process_runtime_owner_mismatch")
        yield
        return
    fence = ProcessSendFence(
        settings,
        observations_required=True,
        observer_owned=True,
        runtime_owner=database,
        closed=True,
    )
    previous = install_process_send_fence(fence)
    observer: asyncio.Task[None] | None = None
    try:
        schema = await read_startup_schema(database)
        if schema == BASELINE_SCHEMA:
            if database.sequences is not None:
                raise BaselineSendClosed("baseline_runtime_configuration_mismatch")
            observer = asyncio.create_task(
                run_baseline_observer(engine=database.engine, fence=fence)
            )
            fence.closed = False
            if wait_baseline:
                try:
                    async with asyncio.timeout(4):
                        while fence.last_observed is None:
                            await asyncio.sleep(0.05)
                except TimeoutError:
                    raise BaselineSendClosed("baseline_process_observation_unavailable") from None
        else:
            fence.candidate_mode = True
            fence.observations_required = False
            if database.sequences is not None:
                from app.infrastructure.reply_sequence_process_health import (
                    require_verified_process_health,
                )

                async def health(bot_resource_id: str) -> None:
                    assert database.sequences is not None
                    await require_verified_process_health(
                        database.sequences, bot_resource_id=bot_resource_id
                    )

                fence.candidate_health = health
            # No callback (all v2 flags off) keeps primary sends closed. Idle DB
            # workers and protected control commands can still bootstrap evidence.
            fence.closed = False
        yield
    finally:
        fence.closed = True
        if observer is not None:
            observer.cancel()
            with suppress(asyncio.CancelledError):
                await observer
        install_process_send_fence(previous)
