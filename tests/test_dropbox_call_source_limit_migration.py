"""Exercise the actual archive migration, including the pre-fix failure."""

import importlib.util
import os
import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/0104_dropbox_call_source_limit.py"
)
LONG_SOURCE_BYTES = 37_585_783
NEW_SOURCE_BYTES = 8_192_803


def _load_migration():
    spec = importlib.util.spec_from_file_location("dropbox_call_source_limit", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compiled_upgrade_enforces_source_bound_without_changing_telegram():
    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    migration.op = Operations(context)
    migration.upgrade()
    sql = output.getvalue()
    assert "telegram" not in sql
    assert "DROP CONSTRAINT ck_phone_call_recordings_bounded_size" in sql
    # Apply the exact emitted CHECK expression to a local relational database.
    condition = sql.split(" CHECK (", 1)[1].split(");", 1)[0]
    with sqlite3.connect(":memory:") as db:
        db.execute(f"CREATE TABLE calls (size_bytes INTEGER CHECK ({condition}))")
        for size in (NEW_SOURCE_BYTES, LONG_SOURCE_BYTES, 100 * 1024 * 1024):
            db.execute("INSERT INTO calls VALUES (?)", (size,))
        for size in (-1, 0, 100 * 1024 * 1024 + 1):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO calls VALUES (?)", (size,))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_old_failure_upgrade_and_lossless_downgrade():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for the PostgreSQL migration regression")
    engine = create_async_engine(database_url)
    migration = _load_migration()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                # Temporary tables shadow production names; all effects roll back.
                for table, constraint in (
                    ("phone_call_recordings", "ck_phone_call_recordings_bounded_size"),
                    ("telegram_voice_archives", "ck_telegram_voice_archives_size"),
                ):
                    await connection.execute(text(
                        f"CREATE TEMP TABLE {table} (size_bytes INTEGER NOT NULL, "
                        f"CONSTRAINT {constraint} CHECK "
                        "(size_bytes > 0 AND size_bytes <= 26214400)) ON COMMIT DROP"
                    ))

                async def insert(size, table="phone_call_recordings"):
                    await connection.execute(
                        text(f"INSERT INTO {table} (size_bytes) VALUES (:size)"),
                        {"size": size},
                    )

                async def rejected(size, table="phone_call_recordings"):
                    with pytest.raises(IntegrityError):
                        async with connection.begin_nested():
                            await insert(size, table)

                def migrate(sync_connection, direction):
                    migration.op = Operations(MigrationContext.configure(sync_connection))
                    getattr(migration, direction)()

                await rejected(LONG_SOURCE_BYTES)
                await connection.run_sync(migrate, "upgrade")
                await insert(LONG_SOURCE_BYTES)
                await insert(NEW_SOURCE_BYTES)
                await insert(100 * 1024 * 1024)
                for invalid in (-1, 0, 100 * 1024 * 1024 + 1):
                    await rejected(invalid)
                await rejected(LONG_SOURCE_BYTES, "telegram_voice_archives")
                with pytest.raises(RuntimeError, match="downgrade_blocked_by_oversized_rows"):
                    await connection.run_sync(migrate, "downgrade")
                assert await connection.scalar(text(
                    "SELECT count(*) FROM phone_call_recordings"
                )) == 3
                # Remove only this disposable test's oversized fixtures.
                await connection.execute(text(
                    "DELETE FROM phone_call_recordings WHERE size_bytes > 26214400"
                ))
                await connection.run_sync(migrate, "downgrade")
                await rejected(LONG_SOURCE_BYTES)
                await insert(NEW_SOURCE_BYTES)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
