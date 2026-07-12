from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.deps import get_db_session
from app.core.config import get_settings
from app.main import app
from app.models.domain import (
    Account,
    Base,
    Bot,
    Conversation,
    Message,
    MessageDirection,
    Project,
    ReplyTask,
    ReplyTaskStatus,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_mcp_query_cannot_cross_account_or_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL isolation test")

    schema = f"mcp_test_{uuid4().hex}"
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

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            allowed_account = UUID("22222222-2222-2222-2222-222222222222")
            denied_account = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            allowed_project = UUID("33333333-3333-3333-3333-333333333333")
            denied_project = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            bot = Bot(id=uuid4(), username=f"gate_test_{uuid4().hex}")
            session.add_all(
                [
                    bot,
                    Account(
                        id=allowed_account,
                        alias=f"allowed_{uuid4().hex}",
                        owner="mark",
                        account_type="telegram",
                    ),
                    Account(
                        id=denied_account,
                        alias=f"denied_{uuid4().hex}",
                        owner="other",
                        account_type="telegram",
                    ),
                    Project(
                        id=allowed_project,
                        alias=f"allowed_{uuid4().hex}",
                        name=f"Allowed {uuid4().hex}",
                        adapter_type="test",
                    ),
                    Project(
                        id=denied_project,
                        alias=f"denied_{uuid4().hex}",
                        name=f"Denied {uuid4().hex}",
                        adapter_type="test",
                    ),
                ]
            )
            await session.flush()
            now = datetime.now(UTC)
            for account_id, project_id, label in (
                (allowed_account, allowed_project, "allowed message"),
                (denied_account, denied_project, "denied message"),
            ):
                conversation = Conversation(
                    id=uuid4(),
                    bot_id=bot.id,
                    account_id=account_id,
                    project_id=project_id,
                    chat_id=str(uuid4()),
                    requires_reply=True,
                    last_message_at=now,
                )
                message = Message(
                    id=uuid4(),
                    bot_id=bot.id,
                    account_id=account_id,
                    project_id=project_id,
                    direction=MessageDirection.INBOUND.value,
                    source="test",
                    event_type="message",
                    chat_id=conversation.chat_id,
                    sender_id="sender",
                    text=label,
                    received_at=now,
                )
                session.add_all([conversation, message])
                await session.flush()
                session.add(
                    ReplyTask(
                        account_id=account_id,
                        project_id=project_id,
                        conversation_id=conversation.id,
                        trigger_message_id=message.id,
                        status=ReplyTaskStatus.NEEDS_REPLY.value,
                        reason="integration_test",
                    )
                )
            await session.flush()

            async def override_session() -> AsyncIterator[AsyncSession]:
                yield session

            app.dependency_overrides[get_db_session] = override_session
            monkeypatch.setenv("GATE_MCP_ENABLED", "true")
            monkeypatch.setenv("MCP_READ_BEARER_TOKEN", "integration-token")
            monkeypatch.setenv("MCP_READ_WORKSPACE_ID", "mark-personal")
            monkeypatch.setenv("MCP_READ_ACCOUNT_IDS", str(allowed_account))
            monkeypatch.setenv("MCP_READ_PROJECT_IDS", str(allowed_project))
            get_settings.cache_clear()

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer integration-token"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "isolation",
                        "method": "tools/call",
                        "params": {"name": "gate_list_unanswered", "arguments": {}},
                    },
                )

            assert response.status_code == 200
            items = response.json()["result"]["structuredContent"]["items"]
            assert [item["message"]["text_preview"] for item in items] == [
                "allowed message"
            ]
            await session.rollback()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
