"""PostgreSQL guards for canonical Business text human-inbox projection."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.canonical_messaging import CanonicalMessageDirection, WorkRoute
from app.application.mcp_inbox import build_unanswered_list
from app.application.telegram_business_text_inbox import (
    TelegramBusinessTextInboxCommand,
)
from app.core.config import get_settings
from app.core.mcp_security import AccessScope
from app.infrastructure.telegram_business_text_inbox import (
    SqlTelegramBusinessTextInboxProjector,
    TelegramBusinessTextInboxConflictError,
)
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import (
    Account,
    AuditLog,
    Base,
    Conversation,
    GateEvent,
    Message,
    Project,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
    TelegramUpdate,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command(
    *,
    account_id: str,
    project_id: str,
    canonical_message_id: str,
    provider_message_id: str,
    direction: CanonicalMessageDirection,
    route: WorkRoute,
    occurred_at: datetime,
    text_content: str = "private canonical text",
) -> TelegramBusinessTextInboxCommand:
    return TelegramBusinessTextInboxCommand(
        account_id=account_id,
        project_id=project_id,
        canonical_message_id=canonical_message_id,
        bot_username="gate_m_bot",
        update_id=int(provider_message_id),
        event_type="business_message",
        direction=direction,
        route=route,
        occurred_at=occurred_at,
        provider_dialog_id="private-dialog",
        provider_message_id=provider_message_id,
        provider_sender_id=(
            "private-person" if direction is CanonicalMessageDirection.INBOUND else "owner"
        ),
        business_connection_id="private-connection",
        text_content=text_content,
        counterparty_user_id="private-person",
        counterparty_display_name="Private Person",
        counterparty_username="private_username",
    )


@pytest.mark.anyio
async def test_canonical_inbox_projection_is_atomic_idempotent_and_closes_on_outbound() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL inbox projection test")

    schema = f"business_text_inbox_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        workspace_id = uuid4()
        connector_id = uuid4()
        dialog_id = uuid4()
        inbound_canonical_id = uuid4()
        outbound_canonical_id = uuid4()
        account_id = uuid4()
        project_id = uuid4()
        occurred_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        async with sessions.begin() as session:
            session.add_all(
                [
                    Workspace(
                        id=workspace_id,
                        key="mark-personal",
                        display_name="Mark personal",
                    ),
                    Account(
                        id=account_id,
                        alias="telegram_inbox_test",
                        owner="owner",
                        account_type="telegram_business",
                        permissions={},
                    ),
                    Project(
                        id=project_id,
                        alias="inbox-project",
                        name="Inbox project",
                        adapter_type="manual",
                    ),
                ]
            )
            await session.flush()
            session.add(
                TransportConnector(
                    id=connector_id,
                    workspace_id=workspace_id,
                    connector_key="telegram_business",
                    connector_kind="telegram_business",
                    is_enabled=True,
                )
            )
            await session.flush()
            session.add(
                Dialog(
                    id=dialog_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    provider_dialog_id="private-dialog",
                    dialog_kind="private",
                )
            )
            await session.flush()
            session.add_all(
                [
                    CanonicalMessage(
                        id=inbound_canonical_id,
                        workspace_id=workspace_id,
                        dialog_id=dialog_id,
                        provider_message_id="101",
                        provider_sender_id="private-person",
                        direction="inbound",
                        text_content="private canonical text",
                        input_origin="telegram_text",
                        occurred_at=occurred_at,
                    ),
                    CanonicalMessage(
                        id=outbound_canonical_id,
                        workspace_id=workspace_id,
                        dialog_id=dialog_id,
                        provider_message_id="102",
                        provider_sender_id="owner",
                        direction="outbound",
                        text_content="private outbound text",
                        input_origin="telegram_text",
                        occurred_at=occurred_at + timedelta(minutes=1),
                    ),
                ]
            )

        inbound = _command(
            account_id=str(account_id),
            project_id=str(project_id),
            canonical_message_id=str(inbound_canonical_id),
            provider_message_id="101",
            direction=CanonicalMessageDirection.INBOUND,
            route=WorkRoute.HUMAN_REPLY_TASK,
            occurred_at=occurred_at,
        )
        async with sessions.begin() as session:
            first = await SqlTelegramBusinessTextInboxProjector(session).project(inbound)
        async with sessions.begin() as session:
            replay = await SqlTelegramBusinessTextInboxProjector(session).project(inbound)
        async with sessions.begin() as session:
            with pytest.raises(
                TelegramBusinessTextInboxConflictError,
                match="telegram_business_text_inbox_identity_conflict",
            ):
                await SqlTelegramBusinessTextInboxProjector(session).project(
                    replace(inbound, text_content="changed private canonical text")
                )

        assert first.message_created is True
        assert first.reply_task_created is True
        assert replay.duplicate is True
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Message)) == 1
            assert await session.scalar(select(func.count()).select_from(ReplyTask)) == 1
            task = (await session.execute(select(ReplyTask))).scalar_one()
            assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
            message = (await session.execute(select(Message))).scalar_one()
            assert message.canonical_message_id == inbound_canonical_id
            assert message.payload == {
                "canonical_source": True,
                "_gate_counterparty": {"display_name": "Private Person"},
            }
            inbox = await build_unanswered_list(
                session=session,
                scope=AccessScope(
                    workspace_id="mark-personal",
                    account_ids=frozenset({account_id}),
                    project_ids=frozenset({project_id}),
                ),
                limit=10,
            )
            assert inbox["count"] == 1
            assert inbox["items"][0]["message"]["text_preview"] == ("private canonical text")

        outbound = _command(
            account_id=str(account_id),
            project_id=str(project_id),
            canonical_message_id=str(outbound_canonical_id),
            provider_message_id="102",
            direction=CanonicalMessageDirection.OUTBOUND,
            route=WorkRoute.IGNORED,
            occurred_at=occurred_at + timedelta(minutes=1),
            text_content="private outbound text",
        )
        async with sessions.begin() as session:
            result = await SqlTelegramBusinessTextInboxProjector(session).project(outbound)

        assert result.reply_task_created is False
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Message)) == 2
            task = (await session.execute(select(ReplyTask))).scalar_one()
            conversation = (await session.execute(select(Conversation))).scalar_one()
            assert task.status == ReplyTaskStatus.REPLIED.value
            assert conversation.requires_reply is False
            assert await session.scalar(select(func.count()).select_from(ReplyDraft)) == 0
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
            assert await session.scalar(select(func.count()).select_from(GateEvent)) == 0
            assert await session.scalar(select(func.count()).select_from(TelegramUpdate)) == 0
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
            inbox = await build_unanswered_list(
                session=session,
                scope=AccessScope(
                    workspace_id="mark-personal",
                    account_ids=frozenset({account_id}),
                    project_ids=frozenset({project_id}),
                ),
                limit=10,
            )
            assert inbox["count"] == 0
            assert inbox["answered_count"] == 1
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
