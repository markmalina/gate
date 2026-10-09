"""Synthetic approved-document retrieval and scope regression tests."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.api import mcp_routes
from app.application.telegram_attachments import validated_telegram_sent_document_payload
from app.core.config import get_settings
from app.core.mcp_security import AccessScope, Principal
from app.models.domain import Account, Base, Bot, Message, Project
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
PROJECT = UUID("22222222-2222-2222-2222-222222222222")
MESSAGE = UUID("33333333-3333-3333-3333-333333333333")
CONTENT = b"synthetic original document"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def acknowledgement() -> dict[str, Any]:
    return {"ok": True, "result": {
        "message_id": 42, "chat": {"id": 300},
        "business_connection_id": "synthetic-connection",
        "document": {
            "file_id": "synthetic-private-file",
            "file_unique_id": "synthetic-private-unique",
            "file_name": "synthetic.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "file_size": len(CONTENT),
        },
    }}


def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
    return validated_telegram_sent_document_payload(
        payload, telegram_message_id=42, chat_id="300",
        business_connection_id="synthetic-connection",
    )


def test_sent_document_acknowledgement_excludes_unrelated_nested_media() -> None:
    raw = acknowledgement()
    raw["document"] = {**raw["result"]["document"], "file_unique_id": "wrong"}
    raw["result"]["reply_to_message"] = {"document": raw["document"]}
    assert validate(raw) == {"document": raw["result"]["document"]}


@pytest.mark.parametrize("change", [
    {"message_id": 43}, {"message_id": True}, {"chat": {"id": 301}},
    {"chat": {"id": True}}, {"chat": None},
    {"business_connection_id": "other-connection"}, {"document": None},
])
def test_mismatched_sent_document_identity_is_rejected(change: dict[str, Any]) -> None:
    raw = acknowledgement()
    raw["result"].update(change)
    assert validate(raw) is None


def test_sent_document_requires_successful_provider_acknowledgement() -> None:
    for change in ({"ok": False}, {"ok": 1}, {"result": None}):
        assert validate({**acknowledgement(), **change}) is None
    raw = acknowledgement()
    del raw["result"]["business_connection_id"]
    assert validate(raw) is not None


def context() -> dict[str, Any]:
    return {"messages": [{"id": str(MESSAGE)}],
            "conversation": {"chat_id": "300",
                             "business_connection_id": "synthetic-connection"}}


def principal() -> Principal:
    return Principal(subject="synthetic", auth_method="test", scopes=frozenset(),
                     access_scope=AccessScope(workspace_id="synthetic",
                                              account_ids=frozenset({ACCOUNT}),
                                              project_ids=frozenset({PROJECT})))


async def retrieve(session: Any, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    return await mcp_routes._attachment_reply_context_tool_result(
        request_id=1, context=snapshot or context(), message_id=MESSAGE, attachment_index=0,
        principal=principal(), session=session,
        settings=SimpleNamespace(telegram_bot_token="synthetic-token"),
    )


@pytest.mark.anyio
async def test_sent_document_download_returns_original_bytes_and_rejects_bad_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads: list[str] = []

    async def download(*, attachment: Any, bot_token: str) -> tuple[bytes, str]:
        assert bot_token == "synthetic-token"
        downloads.append(attachment.file_id)
        return CONTENT, attachment.mime_type

    monkeypatch.setattr(mcp_routes, "_download_telegram_attachment", download)
    message = SimpleNamespace(source="gate-approved-document", payload=acknowledgement(),
                              telegram_message_id=42, telegram_update_id=None,
                              event_type="telegram_business_document", chat_id="300",
                              business_connection_id="synthetic-connection")

    class Session:
        async def execute(self, statement: Any) -> Any:
            return SimpleNamespace(one_or_none=lambda: (message, None, None, None))

    result = await retrieve(Session())
    import base64

    assert result["result"]["isError"] is False
    content = result["result"]["content"]
    resource = next(item["resource"] for item in content if item["type"] == "resource")
    assert base64.b64decode(resource["blob"]) == CONTENT
    assert downloads == ["synthetic-private-file"]

    message.payload["result"]["message_id"] = 43
    rejected = await retrieve(Session())
    assert rejected["result"]["isError"] is True
    assert downloads == ["synthetic-private-file"]


@pytest.mark.anyio
async def test_postgres_sent_document_retrieval_preserves_all_scope_fences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL isolation test")
    schema = f"attachment_test_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}},
    )
    downloads: list[str] = []

    async def download(*, attachment: Any, bot_token: str) -> tuple[bytes, str]:
        downloads.append(attachment.file_id)
        return CONTENT, attachment.mime_type

    monkeypatch.setattr(mcp_routes, "_download_telegram_attachment", download)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            bot = Bot(id=uuid4(), username=f"synthetic_{uuid4().hex}")
            other_account, other_project = uuid4(), uuid4()
            session.add_all([
                bot,
                Account(id=ACCOUNT, alias="synthetic_allowed", owner="synthetic",
                        account_type="telegram"),
                Account(id=other_account, alias="synthetic_denied", owner="synthetic",
                        account_type="telegram"),
                Project(id=PROJECT, alias="synthetic_allowed", name="Synthetic allowed",
                        adapter_type="test"),
                Project(id=other_project, alias="synthetic_denied", name="Synthetic denied",
                        adapter_type="test"),
            ])
            await session.flush()
            message = Message(
                id=MESSAGE, bot_id=bot.id, account_id=ACCOUNT, project_id=PROJECT,
                direction="outbound", source="gate-approved-document",
                event_type="telegram_business_document", chat_id="300", sender_id="synthetic",
                business_connection_id="synthetic-connection", telegram_message_id=42,
                payload=acknowledgement(), sent_at=datetime.now(UTC),
            )
            session.add(message)
            await session.flush()
            accepted = await retrieve(session)
            assert accepted["result"]["isError"] is False
            assert len(downloads) == 1
            for field, denied in [
                ("account_id", other_account), ("project_id", other_project),
                ("chat_id", "301"), ("business_connection_id", "other-connection"),
                ("source", "gate-approved-draft"), ("source", "gate"),
                ("direction", "inbound"), ("event_type", "telegram_business_reply"),
                ("telegram_message_id", 43),
            ]:
                original = getattr(message, field)
                setattr(message, field, denied)
                await session.flush()
                rejected = await retrieve(session)
                assert rejected["result"]["isError"] is True, field
                assert len(downloads) == 1, field
                setattr(message, field, original)
                await session.flush()
            hidden = deepcopy(context())
            hidden["messages"] = []
            assert (await retrieve(session, hidden))["result"]["isError"] is True
            assert len(downloads) == 1
            # Existing inbound Telegram documents remain retrievable.
            message.source = "telegram"
            message.direction = "inbound"
            message.event_type = "business_message"
            await session.flush()
            assert (await retrieve(session))["result"]["isError"] is False
            assert len(downloads) == 2
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()

