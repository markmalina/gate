"""The authored branches converge through a no-DDL join on actual synthetic PostgreSQL."""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from app.core.config import Settings
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.parametrize(
    "baseline",
    [
        "0115_watch_voice_post_payment",
        "0116_reply_dialog_versions",
        "0116_reply_media_packages",
    ],
)
def test_authored_branch_upgrades_to_exact_join_without_rewriting_history(baseline):
    database_url = Settings(_env_file=None).database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for actual migration convergence")
    name = "reply_join_test_" + uuid4().hex
    target = make_url(database_url).set(database=name).render_as_string(hide_password=False)

    async def ddl(statement):
        engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env={**os.environ, "DATABASE_URL": target},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    asyncio.run(ddl(f'CREATE DATABASE "{name}"'))
    try:
        migrate("upgrade", baseline)
        migrate("upgrade", "0120_reply_media_dialog_join")
        assert "0120_reply_media_dialog_join (head)" in migrate("current")
        migrate("check")
    finally:
        asyncio.run(ddl(f'DROP DATABASE "{name}"'))
