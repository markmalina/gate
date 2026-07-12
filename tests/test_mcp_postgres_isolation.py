from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
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
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
    SendAttemptStatus,
    TelegramBusinessConnection,
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
                        alias=f"allowed_{uuid4().hex}_ru",
                        owner="mark",
                        country_code="RU",
                        telegram_user_id=999,
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
            session.add(
                TelegramBusinessConnection(
                    id=uuid4(),
                    account_id=allowed_account,
                    business_connection_id="bc-allowed",
                    telegram_user_id=999,
                    user_chat_id=999,
                    rights={"can_reply": True},
                    is_enabled=True,
                )
            )
            await session.flush()
            now = datetime.now(UTC)
            task_ids: dict[str, UUID] = {}
            conversation_chat_ids: dict[str, str] = {}
            conversation_ids: dict[str, UUID] = {}
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
                    business_connection_id=(
                        "bc-allowed" if label == "allowed message" else "bc-denied"
                    ),
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
                    business_connection_id=(
                        "bc-allowed" if label == "allowed message" else "bc-denied"
                    ),
                    text=label,
                    received_at=now,
                )
                session.add_all([conversation, message])
                conversation_chat_ids[label] = conversation.chat_id
                conversation_ids[label] = conversation.id
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

            service_message = Message(
                id=uuid4(),
                bot_id=bot.id,
                account_id=allowed_account,
                project_id=allowed_project,
                direction=MessageDirection.INBOUND.value,
                source="telegram",
                event_type="business_message",
                chat_id=conversation_chat_ids["allowed message"],
                sender_id="sender",
                text="/webhook",
                received_at=now,
            )
            session.add(service_message)
            await session.flush()
            session.add(
                ReplyTask(
                    id=uuid4(),
                    account_id=allowed_account,
                    project_id=allowed_project,
                    conversation_id=conversation_ids["allowed message"],
                    trigger_message_id=service_message.id,
                    status=ReplyTaskStatus.NEEDS_REPLY.value,
                    reason="legacy_service_message",
                )
            )

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
            system_message = Message(
                id=uuid4(),
                bot_id=bot.id,
                account_id=allowed_account,
                project_id=allowed_project,
                direction=MessageDirection.OUTBOUND.value,
                source="gate",
                event_type="telegram_business_reply",
                chat_id=conversation_chat_ids["allowed message"],
                sender_id="gate_m_bot",
                recipient_id=conversation_chat_ids["allowed message"],
                text="Gate status diagnostic",
                sent_at=now,
            )
            empty_business_event = Message(
                id=uuid4(),
                bot_id=bot.id,
                account_id=allowed_account,
                project_id=allowed_project,
                direction=MessageDirection.OUTBOUND.value,
                source="telegram",
                event_type="business_connection",
                chat_id=conversation_chat_ids["allowed message"],
                sender_id="account-owner",
                recipient_id=conversation_chat_ids["allowed message"],
                text=None,
                sent_at=now,
            )
            owner_self_message = Message(
                id=uuid4(),
                bot_id=bot.id,
                account_id=allowed_account,
                project_id=allowed_project,
                direction=MessageDirection.OUTBOUND.value,
                source="telegram",
                event_type="business_message",
                chat_id="999",
                sender_id="999",
                recipient_id="999",
                text="owner self note",
                sent_at=now,
            )
            session.add_all(
                [
                    answered_message,
                    system_message,
                    empty_business_event,
                    owner_self_message,
                ]
            )
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
                    scopes=frozenset(
                        {"gate:drafts:write", "gate:messages:send"}
                    ),
                    access_scope=AccessScope(
                        workspace_id="mark-personal",
                        account_ids=frozenset({allowed_account}),
                        project_ids=frozenset({allowed_project}),
                    ),
                )

            monkeypatch.setattr("app.api.mcp_routes.authenticate_mcp_oauth", authenticate)
            monkeypatch.setenv("MCP_AUTH_MODE", "oauth")
            monkeypatch.setenv("GATE_SEND_ENABLED", "true")
            monkeypatch.setenv("GATE_ACCOUNT_RU_SEND_ENABLED", "true")
            monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "https://auth.gate.example")
            monkeypatch.setenv("MCP_PUBLIC_RESOURCE_URL", "https://gate.example/mcp")
            monkeypatch.setenv(
                "MCP_OAUTH_JWKS_URL", "https://auth.gate.example/.well-known/jwks.json"
            )
            get_settings.cache_clear()

            class RecordingTelegram:
                def __init__(self) -> None:
                    self.calls: list[dict[str, object]] = []

                async def send_message(self, **kwargs: object) -> dict[str, object]:
                    self.calls.append(kwargs)
                    return {"ok": True, "result": {"message_id": 321}}

            telegram = RecordingTelegram()

            def override_telegram() -> RecordingTelegram:
                return telegram

            app.dependency_overrides[get_telegram_client] = override_telegram

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
            assert allowed.json()["result"]["structuredContent"]["account"]["label"] == "RU"
            allowed_draft_id = allowed.json()["result"]["structuredContent"]["draft_id"]

            send_payload = {
                "jsonrpc": "2.0",
                "id": "send-approved",
                "method": "tools/call",
                "params": {
                    "name": "gate_send_approved_reply",
                    "arguments": {
                        "draft_id": allowed_draft_id,
                        "confirmed": True,
                        "expected_account": "RU",
                        "expected_recipient_chat_id": "sender",
                    },
                },
            }
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                sent = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json=send_payload,
                )
                replayed = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json=send_payload,
                )

            assert sent.json()["result"]["structuredContent"]["sent"] is True
            assert sent.json()["result"]["structuredContent"]["account"]["label"] == "RU"
            assert sent.json()["result"]["structuredContent"]["recipient_chat_id"] == "sender"
            assert replayed.json()["result"]["structuredContent"]["sent"] is True
            assert replayed.json()["result"]["structuredContent"]["idempotent_replay"] is True
            assert len(telegram.calls) == 1
            assert telegram.calls[0] == {
                "chat_id": "sender",
                "text": "Привет! С удовольствием расскажу подробнее.",
                "business_connection_id": "bc-allowed",
            }
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
            assert drafts[0].status == ReplyDraftStatus.APPROVED.value
            attempts = (await session.execute(select(SendAttempt))).scalars().all()
            assert len(attempts) == 1
            assert attempts[0].status == SendAttemptStatus.SENT.value
            sent_messages = (
                (
                    await session.execute(
                        select(Message).where(Message.source == "gate-approved-draft")
                    )
                )
                .scalars()
                .all()
            )
            assert len(sent_messages) == 1
            assert sent_messages[0].text == "Привет! С удовольствием расскажу подробнее."
            await session.rollback()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
