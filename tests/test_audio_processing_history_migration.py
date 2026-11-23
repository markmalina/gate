"""Preserve archived audio and legacy timestamps through the additive migration."""

from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import get_settings
from sqlalchemy import Column, DateTime, Integer, LargeBinary, MetaData, Table, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_processing_history_migration_leaves_historical_values_intact() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for the PostgreSQL migration test")
    source = (
        Path(__file__).resolve().parents[1] / "alembic/versions/0122_audio_processing_history.py"
    )
    spec = spec_from_file_location("audio_processing_history_migration", source)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(database_url)
    fields = {
        "transcription_queued_at", "recognition_started_at", "text_saved_at",
        "transcription_stopped_at", "transcription_stop_reason",
    }

    def verify_migration(connection) -> None:
        metadata = MetaData()
        archived_tables = [
            Table(
                name, metadata,
                Column("id", Integer, primary_key=True),
                Column("data", LargeBinary, nullable=False),
                Column("received_at", DateTime(timezone=True)),
                Column("transcribed_at", DateTime(timezone=True)),
                prefixes=["TEMPORARY"],
            )
            for name in ("watch_voice_notes", "phone_call_recordings", "telegram_voice_archives")
        ]
        metadata.create_all(connection, checkfirst=False)
        received_at = datetime(2026, 11, 20, tzinfo=UTC)
        legacy_time = received_at + timedelta(seconds=30)
        for table in archived_tables:
            connection.execute(table.insert().values(
                id=1,
                data=b"synthetic-archive",
                received_at=received_at,
                transcribed_at=legacy_time,
            ))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        for table in archived_tables:
            migrated = Table(table.name, MetaData(), autoload_with=connection)
            assert fields <= set(migrated.columns.keys())
            record = connection.execute(select(migrated)).mappings().one()
            assert record["data"] == b"synthetic-archive"
            assert (record["received_at"], record["transcribed_at"]) == (received_at, legacy_time)
            assert all(record[field] is None for field in fields)
        migration.downgrade()
        for table in archived_tables:
            columns = {column["name"] for column in inspect(connection).get_columns(table.name)}
            assert columns == set(table.columns.keys())
            assert connection.execute(select(table.c.data)).scalar_one() == b"synthetic-archive"

    try:
        async with engine.connect() as connection, connection.begin():
            await connection.run_sync(verify_migration)
            await connection.rollback()
    finally:
        await engine.dispose()
