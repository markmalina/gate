"""Physical-session observation on schema 0111, no candidate tables required."""

import asyncio
from contextlib import suppress
from uuid import uuid4

import pytest
from app.core.config import Settings, get_settings
from app.infrastructure.baseline_observation import (
    ACTIVITY_SQL,
    BASELINE_SCHEMA,
    process_metadata,
    read_frozen_fleet,
    run_baseline_observer,
)
from app.services.process_send_fence import ProcessSendFence
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_actual_process_session_idle_identity_drain_and_pool_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL required for actual PostgreSQL baseline observation")
    schema = "g1a_" + uuid4().hex
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=3,
        max_overflow=0,
    )
    observer = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE alembic_version (version_num text)"))
            await connection.execute(
                text("INSERT INTO alembic_version VALUES (:v)"),
                {
                    "v": BASELINE_SCHEMA,
                },
            )
            await connection.execute(
                text("CREATE TABLE send_attempts (status text, finished_at timestamptz)")
            )
        monkeypatch.setenv("RAILWAY_REPLICA_ID", "synthetic-replica")
        monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "synthetic-deployment")
        settings = Settings(
            RAILWAY_GIT_COMMIT_SHA="a" * 40,
            GATE_WORKSPACE_ID="synthetic",
            GATE_SEND_ENABLED=False,
            GATE_REPLY_SEQUENCE_WORKER_ENABLED=False,
        )
        fence = ProcessSendFence(settings, observations_required=True)
        observer = asyncio.create_task(run_baseline_observer(engine=engine, fence=fence))
        for _ in range(100):
            if fence.last_observed is not None:
                break
            await asyncio.sleep(0.05)
        assert fence.last_observed is not None, "physical observation must succeed, not skip"
        metadata = process_metadata(settings, boot="a" * 32)
        expected = [
            {
                key: metadata[key]
                for key in ("source", "replica", "deployment", "workspace", "config")
            }
        ]
        async with engine.connect() as connection:
            receipt = await read_frozen_fleet(connection, expected=expected)
            assert receipt["result"] == "FROZEN_DRAINED"
            assert receipt["process_count"] == 1
            assert receipt["provider_inventory_verified"] is False
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO send_attempts (status, finished_at) "
                    "VALUES ('unknown', clock_timestamp())"
                )
            )
        async with engine.connect() as connection:
            receipt = await read_frozen_fleet(connection, expected=expected)
            assert receipt["result"] == "FROZEN_DRAINED"
            assert receipt["unknown"] == receipt["terminal_unknown"] == 1
            assert receipt["incomplete_unknown"] == 0
            assert (await connection.scalar(text("SELECT count(*) FROM send_attempts"))) == 1
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO send_attempts (status) VALUES ('unknown')"))
        async with engine.connect() as connection:
            receipt = await read_frozen_fleet(connection, expected=expected)
            assert receipt["result"] == "BLOCKED_ATTEMPTS"
            assert receipt["unknown"] == 2 and receipt["incomplete_unknown"] == 1
            assert receipt["terminal_unknown"] == 1
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO send_attempts (status, finished_at) VALUES "
                    "('reserved', clock_timestamp()), ('sending', clock_timestamp())"
                )
            )
        async with engine.connect() as connection:
            receipt = await read_frozen_fleet(connection, expected=expected)
            assert receipt["result"] == "BLOCKED_ATTEMPTS"
            # Active statuses block even if their timestamps are inconsistent.
            assert receipt["reserved"] == receipt["sending"] == 1
            assert receipt["terminal_unknown"] == receipt["incomplete_unknown"] == 1
        observer.cancel()
        with suppress(asyncio.CancelledError):
            await observer
        async with engine.begin() as connection:
            # Observer connection is discarded, never returned read-only to pool.
            await connection.execute(text("INSERT INTO send_attempts (status) VALUES ('reserved')"))
            rows = (await connection.execute(text(ACTIVITY_SQL))).all()
            assert rows == []
    finally:
        if observer is not None and not observer.done():
            observer.cancel()
            with suppress(asyncio.CancelledError):
                await observer
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
