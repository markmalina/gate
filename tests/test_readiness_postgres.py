"""PostgreSQL integration coverage for deployment readiness."""

import os
import uuid

import pytest
from app.application.readiness import (
    CheckReadinessService,
    ReadinessComponentStatus,
    ReadinessFailureCode,
    ReadinessResult,
)
from app.infrastructure.readiness import SqlAlchemyAlembicReadinessProbe
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_readiness_tracks_missing_and_multiple_migration_heads() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema = f"test_readiness_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    engine = None
    expected_heads = frozenset({"head_a", "head_b"})
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema}},
        )
        probe = SqlAlchemyAlembicReadinessProbe(
            engine=engine,
            expected_heads=expected_heads,
        )
        service = CheckReadinessService(probe)

        missing_table_snapshot = await probe.inspect()
        assert missing_table_snapshot.current_heads == frozenset()
        assert await service.execute() == ReadinessResult(
            ready=False,
            database=ReadinessComponentStatus.OK,
            migrations=ReadinessComponentStatus.FAILED,
            reason=ReadinessFailureCode.MIGRATION_HEADS_MISMATCH,
        )

        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) PRIMARY KEY)")
            )
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('head_a'), ('head_b')")
            )

        snapshot = await probe.inspect()
        assert snapshot.expected_heads == expected_heads
        assert snapshot.current_heads == expected_heads
        assert await service.execute() == ReadinessResult(
            ready=True,
            database=ReadinessComponentStatus.OK,
            migrations=ReadinessComponentStatus.OK,
            reason=None,
        )

        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM alembic_version WHERE version_num='head_b'"))
        assert await service.execute() == ReadinessResult(
            ready=False,
            database=ReadinessComponentStatus.OK,
            migrations=ReadinessComponentStatus.FAILED,
            reason=ReadinessFailureCode.MIGRATION_HEADS_MISMATCH,
        )
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
