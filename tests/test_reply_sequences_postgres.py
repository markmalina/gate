"""PostgreSQL regression for detached reply-sequence discovery rows."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.worker.reply_sequences import _discover_sequence_rows, _due_items
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.anyio
async def test_discovery_rows_remain_usable_after_rollback_and_session_close() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for reply-sequence PostgreSQL test")

    schema = f"reply_sequence_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE reply_drafts (
                        id uuid PRIMARY KEY,
                        account_id uuid NOT NULL,
                        project_id uuid NOT NULL,
                        reply_task_id uuid NOT NULL,
                        conversation_id uuid NOT NULL,
                        prompt_context jsonb NOT NULL,
                        created_at timestamptz NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE send_attempts (
                        id uuid PRIMARY KEY,
                        reply_draft_id uuid NOT NULL UNIQUE,
                        status varchar(32) NOT NULL,
                        finished_at timestamptz NULL
                    )
                    """
                )
            )

        sessions = async_sessionmaker(engine)
        now = datetime(2026, 7, 24, 6, 0, tzinfo=UTC)
        sequence_id = str(uuid4())
        account_id = uuid4()
        project_id = uuid4()
        conversation_id = uuid4()
        first_draft_id = uuid4()
        second_draft_id = uuid4()

        def prompt_context(position: int, scheduled_at: datetime) -> str:
            return json.dumps(
                {
                    "reply_sequence": {
                        "id": sequence_id,
                        "position": position,
                        "interval_seconds": 60,
                        "scheduled_at": scheduled_at.isoformat(),
                        "state": "scheduled",
                        "expected_account": "BY",
                        "expected_recipient_chat_id": "synthetic-recipient",
                        "principal_subject": "mark",
                        "workspace_id": "mark-personal",
                    }
                }
            )

        async with engine.begin() as connection:
            for position, draft_id in enumerate((first_draft_id, second_draft_id)):
                await connection.execute(
                    text(
                        """
                        INSERT INTO reply_drafts (
                            id,
                            account_id,
                            project_id,
                            reply_task_id,
                            conversation_id,
                            prompt_context,
                            created_at
                        )
                        VALUES (
                            :id,
                            :account_id,
                            :project_id,
                            :reply_task_id,
                            :conversation_id,
                            CAST(:prompt_context AS jsonb),
                            :created_at
                        )
                        """
                    ),
                    {
                        "id": draft_id,
                        "account_id": account_id,
                        "project_id": project_id,
                        "reply_task_id": uuid4(),
                        "conversation_id": conversation_id,
                        "prompt_context": prompt_context(
                            position,
                            now - timedelta(minutes=2 - position),
                        ),
                        "created_at": now + timedelta(seconds=position),
                    },
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO send_attempts (
                        id,
                        reply_draft_id,
                        status,
                        finished_at
                    )
                    VALUES (
                        :id,
                        :reply_draft_id,
                        'sent',
                        :finished_at
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "reply_draft_id": first_draft_id,
                    "finished_at": now - timedelta(seconds=60),
                },
            )

        rows = await _discover_sequence_rows(sessions)
        due = _due_items(rows, now=now)

        assert len(rows) == 2
        assert len(due) == 1
        assert due[0].draft_id == second_draft_id
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
