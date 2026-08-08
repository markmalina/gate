"""PostgreSQL proof for coalesced Beta checkpoint dispatch draining."""

# ruff: noqa: I001

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.infrastructure.beta_checkpoint_dispatch import (
    BetaCheckpointDispatchEnvelope,
    BetaCheckpointDispatchError,
    build_beta_checkpoint_dispatch,
)
from app.infrastructure.beta_checkpoint_dispatch_drain import (
    SessionFactoryBetaCheckpointDispatchDrain,
)
from app.models.beta_space import BetaCheckpointDispatchIntentModel
from app.models.domain import Base

SPACE_ID = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"
NOW = datetime(2026, 8, 8, 18, tzinfo=UTC)


class _Dispatcher:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls: list[tuple[str, int]] = []

    async def dispatch(
        self, *, space_id: str, accepted_sequence: int
    ) -> BetaCheckpointDispatchEnvelope:
        self.calls.append((space_id, accepted_sequence))
        if self.fail_once:
            self.fail_once = False
            raise BetaCheckpointDispatchError("synthetic_unavailable")
        return build_beta_checkpoint_dispatch(
            space_id=space_id,
            accepted_sequence=accepted_sequence,
        )


@pytest.mark.anyio
async def test_dispatch_drain_is_idle_without_intent_and_sends_highest_sequence() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for checkpoint dispatch PostgreSQL test")
    schema = f"beta_dispatch_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        dispatcher = _Dispatcher()
        drain = SessionFactoryBetaCheckpointDispatchDrain(sessions, dispatcher)
        assert (await drain.drain_once(now=NOW)).disposition == "idle"

        async with sessions.begin() as session:
            session.add(
                BetaCheckpointDispatchIntentModel(
                    space_id=SPACE_ID,
                    accepted_sequence=17,
                    dispatched_sequence=0,
                    state="pending",
                    next_attempt_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        result = await drain.drain_once(now=NOW)
        assert result.disposition == "sent"
        assert dispatcher.calls == [(SPACE_ID, 17)]
        assert (await drain.drain_once(now=NOW)).disposition == "idle"
        async with sessions() as session:
            intent = await session.get(BetaCheckpointDispatchIntentModel, SPACE_ID)
            assert intent is not None
            assert intent.state == "sent"
            assert intent.dispatched_sequence == 17
            assert intent.last_error_code is None
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.anyio
async def test_dispatch_failure_defers_with_backoff_then_retries_same_sequence() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for checkpoint dispatch PostgreSQL test")
    schema = f"beta_dispatch_retry_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            session.add(
                BetaCheckpointDispatchIntentModel(
                    space_id=SPACE_ID,
                    accepted_sequence=18,
                    dispatched_sequence=17,
                    state="pending",
                    next_attempt_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        dispatcher = _Dispatcher(fail_once=True)
        drain = SessionFactoryBetaCheckpointDispatchDrain(sessions, dispatcher)
        first = await drain.drain_once(now=NOW)
        assert first.disposition == "deferred"
        assert first.error_code == "synthetic_unavailable"
        assert (await drain.drain_once(now=NOW)).disposition == "idle"

        second = await drain.drain_once(now=NOW + timedelta(seconds=3))
        assert second.disposition == "sent"
        assert dispatcher.calls == [(SPACE_ID, 18), (SPACE_ID, 18)]
        async with sessions() as session:
            intent = await session.scalar(
                select(BetaCheckpointDispatchIntentModel).where(
                    BetaCheckpointDispatchIntentModel.space_id == SPACE_ID
                )
            )
            assert intent is not None
            assert intent.state == "sent"
            assert intent.attempt_count == 2
            assert intent.dispatched_sequence == 18
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.anyio
async def test_expired_sending_dispatch_becomes_unknown_without_resend() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for checkpoint dispatch PostgreSQL test")
    schema = f"beta_dispatch_stale_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            session.add(
                BetaCheckpointDispatchIntentModel(
                    space_id=SPACE_ID,
                    accepted_sequence=19,
                    dispatched_sequence=18,
                    state="sending",
                    attempt_count=1,
                    lease_expires_at=NOW - timedelta(seconds=1),
                    next_attempt_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        dispatcher = _Dispatcher()
        result = await SessionFactoryBetaCheckpointDispatchDrain(
            sessions, dispatcher
        ).drain_once(now=NOW)
        assert result.disposition == "unknown"
        assert result.error_code == "beta_checkpoint_dispatch_lease_expired"
        assert dispatcher.calls == []
        async with sessions() as session:
            intent = await session.get(BetaCheckpointDispatchIntentModel, SPACE_ID)
            assert intent is not None
            assert intent.state == "unknown"
            assert intent.lease_expires_at is None
            assert intent.settled_at == NOW
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
