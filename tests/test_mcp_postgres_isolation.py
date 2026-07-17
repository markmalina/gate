from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.deps import get_db_session
from app.api.mcp_schemas import GATE_DRAFT_OUTPUT_SCHEMA, GATE_SEND_OUTPUT_SCHEMA
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.core.mcp_security import AccessScope, Principal
from app.main import app
from app.models.billing import BillingEntitlementModel
from app.models.canonical import Workspace
from app.models.domain import (
    Account,
    AuditLog,
    Base,
    Bot,
    Conversation,
    Message,
    MessageDirection,
    Person,
    PersonIdentity,
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
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class UnexpectedTelegramClient:
    """Fail if a read-only MCP test reaches the Telegram provider."""

    async def send_message(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test attempted Telegram send")

    async def get_business_connection(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test queried Telegram")


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
            voice_workspace_id = uuid4()
            session.add_all(
                [
                    Workspace(
                        id=voice_workspace_id,
                        key="mark-personal",
                        display_name="Mark",
                        is_active=True,
                    ),
                    BillingEntitlementModel(
                        workspace_id=voice_workspace_id,
                        entitlement_key="gate-telegram-voice-v1",
                        product="gate",
                        scope_kind="telegram_connector",
                        scope_ref="telegram_business",
                        plan_key="gate-voice-initial",
                        state="active",
                        valid_from=now,
                        valid_until=None,
                        hard_limit_microusd=1_000_000,
                    ),
                ]
            )
            await session.flush()
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
            allowed_person = Person(display_name="private-allowed-contact")
            denied_person = Person(display_name="private-denied-contact")
            session.add_all([allowed_person, denied_person])
            await session.flush()
            session.add_all(
                [
                    PersonIdentity(
                        person_id=allowed_person.id,
                        account_id=allowed_account,
                        platform="telegram",
                        platform_user_id="sender",
                        display_name="private-allowed-contact",
                        is_verified=True,
                        metadata_={
                            "name_source": "telegram_contact",
                            "telegram_contact_name": "private-allowed-contact",
                        },
                    ),
                    PersonIdentity(
                        person_id=denied_person.id,
                        account_id=denied_account,
                        platform="telegram",
                        platform_user_id="sender",
                        display_name="private-denied-contact",
                        is_verified=True,
                        metadata_={
                            "name_source": "telegram_contact",
                            "telegram_contact_name": "private-denied-contact",
                        },
                    ),
                    AuditLog(
                        actor="gate-contact-sync",
                        action="telegram_contact_snapshot_imported",
                        account_id=allowed_account,
                        metadata_={"source": "telegram_desktop_export"},
                        created_at=now,
                    ),
                    AuditLog(
                        actor="gate-contact-sync",
                        action="telegram_contact_snapshot_imported",
                        account_id=denied_account,
                        metadata_={"source": "telegram_desktop_export"},
                        created_at=now,
                    ),
                ]
            )
            await session.flush()

            async def override_session() -> AsyncIterator[AsyncSession]:
                yield session

            app.dependency_overrides[get_db_session] = override_session
            app.dependency_overrides[get_telegram_client] = (
                lambda: UnexpectedTelegramClient()
            )
            monkeypatch.setenv("GATE_MCP_ENABLED", "true")
            monkeypatch.setenv("MCP_AUTH_MODE", "temporary_bearer")
            monkeypatch.setenv("MCP_READ_BEARER_TOKEN", "integration-token")
            monkeypatch.setenv("MCP_READ_WORKSPACE_ID", "mark-personal")
            monkeypatch.setenv("MCP_READ_ACCOUNT_IDS", str(allowed_account))
            monkeypatch.setenv("MCP_READ_PROJECT_IDS", str(allowed_project))
            get_settings.cache_clear()

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                status_response = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer integration-token"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "status-isolation",
                        "method": "tools/call",
                        "params": {"name": "gate_get_status", "arguments": {}},
                    },
                )
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

            assert status_response.status_code == 200
            status_payload = status_response.json()["result"]["structuredContent"]
            contact_directory = status_payload["contact_directory"]
            assert len(contact_directory) == 1
            assert contact_directory[0]["account"]["id"] == str(allowed_account)
            assert contact_directory[0]["account"]["label"] == "RU"
            assert contact_directory[0]["imported_contacts"] == 1
            assert contact_directory[0]["last_source"] == "telegram_desktop_export"
            assert contact_directory[0]["last_import_at"] == now.isoformat()
            voice_status = status_payload["telegram_voice"]
            assert voice_status["state"] == "disabled"
            assert voice_status["entitlement_active"] is True
            assert voice_status["hard_limit_microusd"] == 1_000_000
            assert voice_status["remaining_microusd"] == 1_000_000
            assert voice_status["telegram_delivery_allowed"] is False
            status_wire = status_response.text
            assert "private-allowed-contact" not in status_wire
            assert "private-denied-contact" not in status_wire
            assert '"platform_user_id"' not in status_wire

            assert response.status_code == 200
            structured = response.json()["result"]["structuredContent"]
            items = structured["items"]
            assert [item["message"]["text_preview"] for item in items] == ["allowed message"]
            assert [item["message"]["direction"] for item in items] == ["inbound"]
            assert [item["counterparty"]["label"] for item in items] == [
                "private-allowed-contact"
            ]
            assert "private-denied-contact" not in response.text
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

                async def send_plain_message(self, **kwargs: object) -> dict[str, object]:
                    self.calls.append(kwargs)
                    return {"ok": True, "result": {"message_id": 321}}

                async def get_business_connection(
                    self, *, business_connection_id: str
                ) -> dict[str, object]:
                    return {
                        "ok": True,
                        "result": {
                            "id": business_connection_id,
                            "is_enabled": True,
                            "rights": {"can_reply": True},
                        },
                    }

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
                follow_up = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "follow-up-draft",
                        "method": "tools/call",
                        "params": {
                            "name": "gate_save_reply_draft",
                            "arguments": {
                                "task_id": str(task_ids["allowed message"]),
                                "text": "И ещё одно подтверждённое сообщение.",
                            },
                        },
                    },
                )

            denied_payload = denied.json()["result"]["structuredContent"]
            allowed_payload = allowed.json()["result"]["structuredContent"]
            follow_up_payload = follow_up.json()["result"]["structuredContent"]
            draft_validator = Draft202012Validator(
                GATE_DRAFT_OUTPUT_SCHEMA,
                format_checker=FormatChecker(),
            )
            draft_validator.validate(denied_payload)
            draft_validator.validate(allowed_payload)
            draft_validator.validate(follow_up_payload)
            assert denied.json()["result"]["isError"] is True
            assert denied_payload["error"] == "task_not_found"
            assert allowed.json()["result"]["isError"] is False
            assert allowed_payload["sent"] is False
            assert allowed_payload["account"]["label"] == "RU"
            assert follow_up.json()["result"]["isError"] is False
            assert follow_up_payload["sent"] is False
            allowed_draft_id = allowed_payload["draft_id"]
            follow_up_draft_id = follow_up_payload["draft_id"]

            def send_payload(*, request_id: str, draft_id: str) -> dict[str, object]:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "gate_send_approved_reply",
                        "arguments": {
                            "draft_id": draft_id,
                            "confirmed": True,
                            "expected_account": "RU",
                            "expected_recipient_chat_id": "sender",
                        },
                    },
                }

            first_send_payload = send_payload(
                request_id="send-approved",
                draft_id=allowed_draft_id,
            )
            follow_up_send_payload = send_payload(
                request_id="send-follow-up",
                draft_id=follow_up_draft_id,
            )
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                sent = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json=first_send_payload,
                )
                followed_up = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json=follow_up_send_payload,
                )
                replayed = await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer signed-token"},
                    json=follow_up_send_payload,
                )

            sent_payload = sent.json()["result"]["structuredContent"]
            followed_up_payload = followed_up.json()["result"]["structuredContent"]
            replayed_payload = replayed.json()["result"]["structuredContent"]
            send_validator = Draft202012Validator(
                GATE_SEND_OUTPUT_SCHEMA,
                format_checker=FormatChecker(),
            )
            send_validator.validate(sent_payload)
            send_validator.validate(followed_up_payload)
            send_validator.validate(replayed_payload)
            assert sent_payload["sent"] is True
            assert sent_payload["account"]["label"] == "RU"
            assert sent_payload["recipient_chat_id"] == "sender"
            assert followed_up_payload["sent"] is True
            assert followed_up_payload["idempotent_replay"] is False
            assert replayed_payload["sent"] is True
            assert replayed_payload["idempotent_replay"] is True
            assert len(telegram.calls) == 2
            assert telegram.calls == [
                {
                    "chat_id": "sender",
                    "text": "Привет! С удовольствием расскажу подробнее.",
                    "business_connection_id": "bc-allowed",
                },
                {
                    "chat_id": "sender",
                    "text": "И ещё одно подтверждённое сообщение.",
                    "business_connection_id": "bc-allowed",
                },
            ]
            drafts = (
                (await session.execute(select(ReplyDraft).order_by(ReplyDraft.created_at)))
                .scalars()
                .all()
            )
            assert [
                (draft.account_id, draft.project_id, draft.draft_text)
                for draft in drafts
            ] == [
                (
                    allowed_account,
                    allowed_project,
                    "Привет! С удовольствием расскажу подробнее.",
                ),
                (
                    allowed_account,
                    allowed_project,
                    "И ещё одно подтверждённое сообщение.",
                ),
            ]
            assert all(
                draft.status == ReplyDraftStatus.APPROVED.value for draft in drafts
            )
            attempts = (await session.execute(select(SendAttempt))).scalars().all()
            assert len(attempts) == 2
            assert all(
                attempt.status == SendAttemptStatus.SENT.value for attempt in attempts
            )
            sent_messages = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.source == "gate-approved-draft")
                        .order_by(Message.created_at)
                    )
                )
                .scalars()
                .all()
            )
            assert [message.text for message in sent_messages] == [
                "Привет! С удовольствием расскажу подробнее.",
                "И ещё одно подтверждённое сообщение.",
            ]
            await session.rollback()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
