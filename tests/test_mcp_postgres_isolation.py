from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.deps import get_db_session
from app.core.config import get_settings
from app.core.mcp_security import AccessScope, Principal
from app.main import app
from app.models.domain import (
    Account,
    Base,
    Bot,
    Conversation,
    Message,
    MessageDirection,
    Project,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_mcp_reads_and_drafts_cannot_cross_account_or_project(
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
            task_ids: dict[str, UUID] = {}
            conversation_chat_ids: dict[str, str] = {}
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
                conversation_chat_ids[label] = conversation.chat_id
                await session.flush()
                task = ReplyTask(
                    id=uuid4(),
                    account_id=account_id,
                    project_id=project_id,
                    conversation_id=conversation.id,
                    trigger_message_id=message.id,
                    status=ReplyTaskStatus.NEEDS_REPLY.value,
                    reason="integration_test",
                )
                session.add(task)
                task_ids[label] = task.id

            answered_message = Message(
                id=uuid4(),
                bot_id=bot.id,
                account_id=allowed_account,
                project_id=allowed_project,
                direction=MessageDirection.OUTBOUND.value,
                source="telegram",
                event_type="business_message",
                chat_id=conversation_chat_ids["allowed message"],
                sender_id="account-owner",
                recipient_id=conversation_chat_ids["allowed message"],
                text="allowed answer",
                sent_at=now,
            )
            session.add(answered_message)
            await session.flush()

            async def override_session() -> AsyncIterator[AsyncSession]:
                yield session

            app.dependency_overrides[get_db_session] = override_session
            monkeypatch.setenv("GATE_MCP_ENABLED", "true")
            monkeypatch.setenv("MCP_AUTH_MODE", "temporary_bearer")
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
            structured = response.json()["result"]["structuredContent"]
            items = structured["items"]
            assert [item["message"]["text_preview"] for item in items] == ["allowed message"]
            assert [item["message"]["direction"] for item in items] == ["inbound"]
            assert [
                item["message"]["text_preview"] for item in structured["answered_items"]
            ] == ["allowed answer"]
            assert [
                item["message"]["direction"] for item in structured["answered_items"]
            ] == ["outbound"]

            async def authenticate(**_kwargs: object) -> Principal:
                return Principal(
                    subject="integration-user",
                    auth_method="oauth2",
                    scopes=frozenset({"gate:drafts:write"}),
                    access_scope=AccessScope(
                        workspace_id="mark-personal",
                        account_ids=frozenset({allowed_account}),
                        project_ids=frozenset({allowed_project}),
                    ),
                )

            monkeypatch.setattr("app.api.mcp_routes.authenticate_mcp_oauth", authenticate)
            monkeypatch.setenv("MCP_AUTH_MODE", "oauth")
            monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "https://auth.gate.example")
            monkeypatch.setenv("MCP_PUBLIC_RESOURCE_URL", "https://gate.example/mcp")
            monkeypatch.setenv(
                "MCP_OAUTH_JWKS_URL", "https://auth.gate.example/.well-known/jwks.json"
            )
            get_settings.cache_clear()

            async with AsyncClient(transport=transport, base_url="http://test") as client:
                denied = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "denied-draft",
                        "method": "tools/call",
                        "params": {
                            "name": "gate_save_reply_draft",
                            "arguments": {
                                "task_id": str(task_ids["denied message"]),
                                "text": "Must not be saved",
                            },
                        },
                    },
                )
                allowed = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "allowed-draft",
                        "method": "tools/call",
                        "params": {
                            "name": "gate_save_reply_draft",
                            "arguments": {
                                "task_id": str(task_ids["allowed message"]),
                                "text": "Привет! С удовольствием расскажу подробнее.",
                            },
                        },
                    },
                )

            assert denied.json()["result"]["isError"] is True
            assert denied.json()["result"]["structuredContent"]["error"] == "task_not_found"
            assert allowed.json()["result"]["isError"] is False
            assert allowed.json()["result"]["structuredContent"]["sent"] is False
            drafts = (
                (await session.execute(select(ReplyDraft).order_by(ReplyDraft.created_at)))
                .scalars()
                .all()
            )
            assert [(draft.account_id, draft.project_id, draft.draft_text) for draft in drafts] == [
                (
                    allowed_account,
                    allowed_project,
                    "Привет! С удовольствием расскажу подробнее.",
                )
            ]
            await session.rollback()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
