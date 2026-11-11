"""PostgreSQL concurrency guards for explicitly approved reply delivery."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from app.application.reply_drafts import (
    ReplyTaskClosedError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.reply_sends import (
    ReplySendError,
    ReplySendResult,
    SendApprovedReplyCommand,
)
from app.application.reply_sequences import (
    QueueApprovedReplySequenceCommand,
    QueueApprovedReplySequenceService,
    ReplySequenceError,
)
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.dialog_snapshots import dialog_snapshot
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.models import canonical, relationship_crm  # noqa: F401
from app.models.domain import (
    Account,
    AuditLog,
    Base,
    Bot,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    Project,
    ReplyDocument,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
    SendAttemptStatus,
    TelegramBusinessConnection,
)
from app.services.telegram import TelegramApiError
from app.worker.reply_sequences import run_reply_sequence_once
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class _SeededReply:
    bot_id: UUID
    account_id: UUID
    project_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    task_id: UUID
    draft_ids: tuple[UUID, ...]
    business_connection_id: str
    recipient_chat_id: str
    context_snapshot: str = ""


@asynccontextmanager
async def _postgres_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = Settings(_env_file=None).database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL reply-send tests")

    schema = f"reply_send_test_{uuid4().hex}"
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
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def _seed_reply(
    sessions: async_sessionmaker[AsyncSession],
    *,
    draft_count: int = 1,
    draft_text: str | None = None,
) -> _SeededReply:
    now = datetime.now(UTC)
    bot_id = uuid4()
    project_id = uuid4()
    account_id = uuid4()
    conversation_id = uuid4()
    trigger_message_id = uuid4()
    task_id = uuid4()
    draft_ids = tuple(uuid4() for _ in range(draft_count))
    connection_id = f"bc-{uuid4().hex}"
    recipient = "12345"

    async with sessions() as session:
        session.add_all(
            [
                Project(
                    id=project_id,
                    alias=f"project-{project_id.hex}",
                    name=f"Project {project_id.hex}",
                    adapter_type="telegram",
                    is_active=True,
                ),
                Bot(
                    id=bot_id,
                    username=f"gate_test_{bot_id.hex}",
                    is_active=True,
                ),
            ]
        )
        await session.flush()
        session.add(
            Account(
                id=account_id,
                alias=f"telegram_ru_{account_id.hex}",
                owner="mark-personal",
                country_code="RU",
                telegram_user_id=999,
                account_type="personal",
                linked_project_id=project_id,
                permissions={},
                read_enabled=True,
                send_enabled=True,
                is_active=True,
            )
        )
        await session.flush()
        session.add_all(
            [
                Conversation(
                    id=conversation_id,
                    bot_id=bot_id,
                    account_id=account_id,
                    project_id=project_id,
                    chat_id=recipient,
                    business_connection_id=connection_id,
                    status=ConversationStatus.OPEN.value,
                    requires_reply=True,
                    last_message_at=now,
                    last_inbound_at=now,
                ),
                Message(
                    id=trigger_message_id,
                    bot_id=bot_id,
                    account_id=account_id,
                    project_id=project_id,
                    direction=MessageDirection.INBOUND.value,
                    source="telegram",
                    event_type="business_message",
                    chat_id=recipient,
                    sender_id=recipient,
                    text="Initial inbound message",
                    business_connection_id=connection_id,
                    payload={},
                    received_at=now,
                ),
                TelegramBusinessConnection(
                    id=uuid4(),
                    account_id=account_id,
                    business_connection_id=connection_id,
                    telegram_user_id=999,
                    user_chat_id=999,
                    rights={"can_reply": True},
                    is_enabled=True,
                    received_at=now,
                    last_update_at=now,
                ),
            ]
        )
        await session.flush()
        session.add(
            ReplyTask(
                id=task_id,
                account_id=account_id,
                project_id=project_id,
                conversation_id=conversation_id,
                trigger_message_id=trigger_message_id,
                status=ReplyTaskStatus.NEEDS_REPLY.value,
                priority=100,
                reason="reply_send_concurrency_test",
            )
        )
        await session.flush()
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        context_snapshot = await dialog_snapshot(session, scope=AccessScope(
            workspace_id="mark-personal", account_ids=frozenset({account_id}),
            project_ids=frozenset({project_id}),
        ), conversation=conversation)
        session.add_all(
            [
                ReplyDraft(
                    id=draft_id,
                    account_id=account_id,
                    project_id=project_id,
                    reply_task_id=task_id,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    status=ReplyDraftStatus.GENERATED.value,
                    source="reply_send_concurrency_test",
                    draft_text=(
                        draft_text if draft_text is not None else f"Approved draft {index}"
                    ),
                    prompt_context={"dialog_snapshot": context_snapshot},
                )
                for index, draft_id in enumerate(draft_ids, start=1)
            ]
        )
        await session.commit()

    return _SeededReply(
        bot_id=bot_id,
        account_id=account_id,
        project_id=project_id,
        conversation_id=conversation_id,
        trigger_message_id=trigger_message_id,
        task_id=task_id,
        draft_ids=draft_ids,
        business_connection_id=connection_id,
        recipient_chat_id=recipient,
        context_snapshot=context_snapshot,
    )


async def _add_reply_task_and_review_current_dialog(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: _SeededReply,
) -> tuple[UUID, UUID]:
    message_id = uuid4()
    task_id = uuid4()
    draft_id = uuid4()
    received_at = datetime.now(UTC) + timedelta(seconds=1)
    async with sessions() as session:
        conversation = await session.get(Conversation, seed.conversation_id)
        assert conversation is not None
        session.add_all(
            [
                Message(
                    id=message_id,
                    bot_id=seed.bot_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    direction=MessageDirection.INBOUND.value,
                    source="telegram",
                    event_type="business_message",
                    chat_id=seed.recipient_chat_id,
                    sender_id=seed.recipient_chat_id,
                    text="A second pending reply task",
                    business_connection_id=seed.business_connection_id,
                    payload={},
                    received_at=received_at,
                ),
                ReplyTask(
                    id=task_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    conversation_id=seed.conversation_id,
                    trigger_message_id=message_id,
                    status=ReplyTaskStatus.NEEDS_REPLY.value,
                    priority=100,
                    reason="second_concurrent_reply_task",
                ),
                ReplyDraft(
                    id=draft_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    reply_task_id=task_id,
                    conversation_id=seed.conversation_id,
                    trigger_message_id=message_id,
                    status=ReplyDraftStatus.GENERATED.value,
                    source="second_concurrent_reply_task",
                    draft_text="Approved draft for the second task",
                    prompt_context={},
                ),
            ]
        )
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
        conversation.last_inbound_at = received_at
        conversation.last_message_at = received_at
        # These concurrency/binding fixtures explicitly review the newer dialog
        # for every draft. Stale baseline behavior has separate ingress tests.
        await session.flush()
        reviewed = await dialog_snapshot(
            session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
            conversation=conversation,
        )
        drafts = (await session.scalars(select(ReplyDraft).where(
            ReplyDraft.id.in_(seed.draft_ids + (draft_id,)),
        ))).all()
        for draft in drafts:
            draft.prompt_context = {**(draft.prompt_context or {}), "dialog_snapshot": reviewed}
        await session.commit()
    return task_id, draft_id


def _command(seed: _SeededReply, draft_id: UUID) -> SendApprovedReplyCommand:
    return SendApprovedReplyCommand(
        principal=Principal(
            subject="reply-send-postgres-test",
            auth_method="oauth2",
            scopes=frozenset({"gate:messages:send"}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({seed.account_id}),
                project_ids=frozenset({seed.project_id}),
            ),
        ),
        draft_id=draft_id,
        confirmed=True,
        expected_account="RU",
        expected_recipient_chat_id=seed.recipient_chat_id,
    )


def _save_draft_command(seed: _SeededReply, text_value: str) -> SaveReplyDraftCommand:
    return SaveReplyDraftCommand(
        principal=Principal(
            subject="reply-draft-postgres-test",
            auth_method="oauth2",
            scopes=frozenset({"gate:drafts:write"}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({seed.account_id}),
                project_ids=frozenset({seed.project_id}),
            ),
        ),
        task_id=seed.task_id,
        text=text_value,
        expected_context_snapshot=seed.context_snapshot,
    )


async def _wait_for_backend_lock(
    sessions: async_sessionmaker[AsyncSession],
    *,
    backend_pid: int,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        async with sessions() as observer:
            wait_event_type = await observer.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :backend_pid"),
                {"backend_pid": backend_pid},
            )
        if wait_event_type == "Lock":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("database backend did not reach the expected lock wait")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        GATE_SEND_ENABLED=True,
        GATE_ACCOUNT_RU_SEND_ENABLED=True,
    )


def _live_connection(business_connection_id: str) -> dict[str, object]:
    return {
        "ok": True,
        "result": {
            "id": business_connection_id,
            "is_enabled": True,
            "rights": {"can_reply": True},
        },
    }


async def _send_once(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: _SeededReply,
    draft_id: UUID,
    telegram: Any,
) -> ReplySendResult:
    async with sessions() as session:
        return await SqlTelegramReplySender(
            session=session,
            telegram=telegram,
            settings=_settings(),
        ).send(_command(seed, draft_id))


class _LiteralRecordingTelegram:
    def __init__(self) -> None:
        self.plain_calls: list[dict[str, object]] = []

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **kwargs: object) -> dict[str, object]:
        self.plain_calls.append(kwargs)
        return {"ok": True, "result": {"message_id": 700}}

    async def send_message(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("approved replies must use literal Telegram delivery")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "draft_text",
    [
        "ordinary literal text",
        "Keep <code>this</code> literal.",
        "Keep <pre>this block</pre> literal.",
        "Draft created: 01234567-89ab-cdef-0123-456789abcdef",
        "Edit draft:\nhttps://gate.example/admin/drafts/example",
    ],
)
async def test_approved_reply_uses_exact_literal_delivery(draft_text: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_text=draft_text)
        telegram = _LiteralRecordingTelegram()

        result = await _send_once(
            sessions,
            seed=seed,
            draft_id=seed.draft_ids[0],
            telegram=telegram,
        )

        assert result.sent is True
        assert telegram.plain_calls == [
            {
                "chat_id": seed.recipient_chat_id,
                "text": draft_text,
                "business_connection_id": seed.business_connection_id,
            }
        ]


@pytest.mark.anyio
async def test_draft_writer_waits_on_conversation_before_locking_context() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        writer_task: asyncio.Task[Any] | None = None

        async with sessions() as holder, sessions() as writer_session:
            await holder.execute(text("SET LOCAL lock_timeout = '750ms'"))
            await holder.execute(
                select(Conversation.id)
                .where(Conversation.id == seed.conversation_id)
                .with_for_update()
            )
            backend_pid = await writer_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(backend_pid, int)
            writer_task = asyncio.create_task(
                SaveReplyDraftService(SqlReplyDraftWriter(writer_session)).execute(
                    _save_draft_command(seed, "A new explicitly saved draft")
                )
            )

            try:
                await _wait_for_backend_lock(
                    sessions,
                    backend_pid=backend_pid,
                )

                # A writer waiting for the conversation must not hold any child
                # context row needed by a reservation or outcome transaction.
                await holder.execute(
                    select(ReplyTask.id).where(ReplyTask.id == seed.task_id).with_for_update()
                )
                await holder.execute(
                    select(Message.id)
                    .where(Message.id == seed.trigger_message_id)
                    .with_for_update()
                )
                await holder.execute(
                    select(Account.id).where(Account.id == seed.account_id).with_for_update()
                )
                await holder.commit()

                saved = await asyncio.wait_for(
                    asyncio.shield(writer_task),
                    timeout=5,
                )
                await writer_session.commit()
            finally:
                if holder.in_transaction():
                    await holder.rollback()
                if writer_task is not None and not writer_task.done():
                    writer_task.cancel()
                if writer_task is not None:
                    await asyncio.gather(writer_task, return_exceptions=True)
                if writer_session.in_transaction():
                    await writer_session.rollback()

        assert saved.task_id == seed.task_id
        assert saved.account_id == seed.account_id
        assert saved.project_id == seed.project_id
        assert saved.account_label == "RU"
        assert saved.recipient_chat_id == seed.recipient_chat_id
        assert saved.status == ReplyDraftStatus.GENERATED.value
        assert saved.text == "A new explicitly saved draft"

        async with sessions() as observer:
            draft_count = await observer.scalar(
                select(func.count())
                .select_from(ReplyDraft)
                .where(ReplyDraft.reply_task_id == seed.task_id)
            )
            audits = (
                (
                    await observer.execute(
                        select(AuditLog).where(AuditLog.action == "reply_draft_saved_from_mcp")
                    )
                )
                .scalars()
                .all()
            )
            outbound_count = await observer.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert draft_count == 2
        assert len(audits) == 1
        assert audits[0].metadata_["draft_id"] == str(saved.draft_id)
        assert audits[0].metadata_["task_id"] == str(seed.task_id)
        assert audits[0].metadata_["sent"] is False
        assert outbound_count == 0


def _sequence_command(
    seed: _SeededReply, draft_ids: tuple[UUID, ...]
) -> QueueApprovedReplySequenceCommand:
    return QueueApprovedReplySequenceCommand(
        principal=_command(seed, draft_ids[0]).principal,
        draft_ids=draft_ids,
        confirmed=True,
        expected_account="RU",
        expected_recipient_chat_id=seed.recipient_chat_id,
    )


@pytest.mark.anyio
async def test_fifty_reply_drafts_commit_atomically_in_requested_order() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=50)
        ordered = tuple(reversed(seed.draft_ids))
        now = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)
        command = _sequence_command(seed, ordered)

        async with sessions() as session:
            result = await QueueApprovedReplySequenceService(
                SqlReplySequenceWriter(session, now=now)
            ).execute(command)
            await session.commit()

        assert len(result.items) == 50
        assert [item.draft_id for item in result.items] == list(ordered)
        assert [item.position for item in result.items] == list(range(50))
        assert result.items[-1].scheduled_at == now + timedelta(minutes=49)

        async with sessions() as session:
            drafts = (
                (await session.execute(
                    select(ReplyDraft).where(ReplyDraft.id.in_(seed.draft_ids))
                )).scalars().all()
            )
            metadata = {
                draft.id: draft.prompt_context["reply_sequence"] for draft in drafts
            }
            assert len(metadata) == 50
            assert all(value["size"] == 50 for value in metadata.values())
            assert all(
                metadata[draft_id]["position"] == index
                and metadata[draft_id]["id"] == str(result.sequence_id)
                for index, draft_id in enumerate(ordered)
            )
            audits = (
                (await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "reply_sequence_queued_from_mcp"
                    )
                )).scalars().all()
            )
            assert len(audits) == 1
            with pytest.raises(ReplySequenceError) as raised:
                await QueueApprovedReplySequenceService(
                    SqlReplySequenceWriter(session)
                ).execute(command)
            assert raised.value.code == "sequence_already_queued"
            await session.rollback()


@pytest.mark.anyio
async def test_fifty_item_worker_restart_preserves_order_without_duplicate_send(
    monkeypatch,
) -> None:
    class DistinctTelegram(_LiteralRecordingTelegram):
        async def send_plain_message(self, **kwargs: object) -> dict[str, object]:
            self.plain_calls.append(kwargs)
            return {"ok": True, "result": {"message_id": 1000 + len(self.plain_calls)}}

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=50)
        queued_at = datetime.now(UTC) - timedelta(hours=2)
        async with sessions() as session:
            await QueueApprovedReplySequenceService(
                SqlReplySequenceWriter(session, now=queued_at)
            ).execute(_sequence_command(seed, seed.draft_ids))
            await session.commit()

        telegram = DistinctTelegram()
        observed_at = datetime.now(UTC)
        from app.infrastructure import reply_sends, reply_sequence_state

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return observed_at + timedelta(minutes=position + 1)

        monkeypatch.setattr(reply_sends, "datetime", Clock)
        monkeypatch.setattr(reply_sequence_state, "datetime", Clock)
        for position in range(50):
            assert await run_reply_sequence_once(
                settings=_settings(),
                session_factory=sessions,
                telegram=telegram,
                now=observed_at + timedelta(minutes=position + 1),
            ) == 1
        assert len(telegram.plain_calls) == 50
        assert [call["text"] for call in telegram.plain_calls] == [
            f"Approved draft {position}" for position in range(1, 51)
        ]

        # A fresh worker invocation recovers the completed series from PostgreSQL.
        assert await run_reply_sequence_once(
            settings=_settings(),
            session_factory=sessions,
            telegram=telegram,
            now=observed_at + timedelta(hours=3),
        ) == 0
        assert len(telegram.plain_calls) == 50
        async with sessions() as session:
            attempts = (
                (await session.execute(select(SendAttempt))).scalars().all()
            )
            assert len(attempts) == 50
            assert all(attempt.status == SendAttemptStatus.SENT.value for attempt in attempts)


@pytest.mark.anyio
async def test_fiftieth_draft_binding_mismatch_cannot_partially_queue() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=50)
        _, other_draft = await _add_reply_task_and_review_current_dialog(
            sessions, seed=seed
        )
        command = _sequence_command(seed, seed.draft_ids[:49] + (other_draft,))

        async with sessions() as session:
            with pytest.raises(ReplySequenceError) as raised:
                await QueueApprovedReplySequenceService(
                    SqlReplySequenceWriter(session)
                ).execute(command)
            assert raised.value.code == "sequence_binding_mismatch"
            await session.rollback()

        async with sessions() as session:
            drafts = (
                (await session.execute(
                    select(ReplyDraft).where(
                        ReplyDraft.id.in_(seed.draft_ids + (other_draft,))
                    )
                )).scalars().all()
            )
            assert all("reply_sequence" not in draft.prompt_context for draft in drafts)
            assert await session.scalar(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "reply_sequence_queued_from_mcp"
                )
            ) == 0


async def _attach_document(
    sessions: async_sessionmaker[AsyncSession], seed: _SeededReply, *,
    filename: str = "bundle.zip", media_type: str = "application/zip",
    data: bytes = b"PK\x03\x04selected document bytes",
) -> bytes:
    async with sessions() as session:
        draft = await session.get(ReplyDraft, seed.draft_ids[0])
        assert draft is not None
        draft.source = "chatgpt-mcp-document"
        draft.draft_text = filename
        draft.prompt_context = {**(draft.prompt_context or {}), "kind": "document"}
        session.add(
            ReplyDocument(
                reply_draft_id=draft.id,
                principal_subject="reply-send-postgres-test",
                filename=filename,
                media_type=media_type,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                caption="Ready",
                data=data,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()
    return data


def _document_command(
    seed: _SeededReply, data: bytes, *, digest: str | None = None
) -> SendApprovedReplyCommand:
    return replace(
        _command(seed, seed.draft_ids[0]),
        expected_document_filename="bundle.zip",
        expected_document_size_bytes=len(data),
        expected_document_sha256=digest or hashlib.sha256(data).hexdigest(),
        expected_document_caption="Ready",
    )


class _DocumentTelegram:
    def __init__(self, *, timeout: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.timeout = timeout

    async def get_business_connection(
        self, *, business_connection_id: str
    ) -> dict[str, object]:
        return _live_connection(business_connection_id)

    async def send_business_document(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.timeout:
            request = httpx.Request("POST", "https://api.telegram.org/sendDocument")
            raise httpx.ReadTimeout("unknown", request=request)
        return {
            "ok": True,
            "result": {
                "message_id": 702,
                "chat": {"id": int(kwargs["chat_id"])},
                "business_connection_id": kwargs["business_connection_id"],
                "caption": kwargs["caption"],
                "document": {
                    "file_id": "opaque-file-id",
                    "file_unique_id": "opaque-file-unique-id",
                    "file_name": kwargs["filename"],
                    "mime_type": kwargs["media_type"],
                    "file_size": len(kwargs["data"]),
                },
            },
        }

    async def send_plain_message(self, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Document must never use the text transport")


@pytest.mark.anyio
async def test_document_send_is_bound_and_exact_replay_never_uploads_twice() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        data = await _attach_document(sessions, seed)
        telegram = _DocumentTelegram()
        command = _document_command(seed, data)

        async with sessions() as session:
            first = await SqlTelegramReplySender(
                session=session,
                telegram=telegram,
                settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
            ).send(command)
        async with sessions() as session:
            replay = await SqlTelegramReplySender(
                session=session,
                telegram=telegram,
                settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
            ).send(command)

        assert first.sent is True
        assert first.provider_message_id == "702"
        assert replay.idempotent_replay is True
        assert replay.attempt_id == first.attempt_id
        assert len(telegram.calls) == 1
        assert telegram.calls[0]["data"] == data
        async with sessions() as session:
            stored = await session.scalar(
                select(ReplyDocument).where(ReplyDocument.reply_draft_id == command.draft_id)
            )
            outbound = await session.scalar(
                select(Message).where(Message.source == "gate-approved-document")
            )
        assert stored is not None and stored.data is None
        assert outbound is not None
        assert outbound.payload["result"]["document"]["file_name"] == "bundle.zip"


@pytest.mark.anyio
async def test_document_mismatch_and_text_route_fail_before_telegram() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        data = await _attach_document(sessions, seed)
        telegram = _DocumentTelegram()
        for command in (
            _command(seed, seed.draft_ids[0]),
            _document_command(seed, data, digest="0" * 64),
            replace(_document_command(seed, data), expected_account="BY"),
        ):
            async with sessions() as session:
                with pytest.raises(ReplySendError):
                    await SqlTelegramReplySender(
                        session=session,
                        telegram=telegram,
                        settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
                    ).send(command)
        assert telegram.calls == []


@pytest.mark.anyio
async def test_document_timeout_is_unknown_and_does_not_retry() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        data = await _attach_document(sessions, seed)
        telegram = _DocumentTelegram(timeout=True)
        command = _document_command(seed, data)
        async with sessions() as session:
            first = await SqlTelegramReplySender(
                session=session,
                telegram=telegram,
                settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
            ).send(command)
        async with sessions() as session:
            replay = await SqlTelegramReplySender(
                session=session,
                telegram=telegram,
                settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
            ).send(command)
        assert first.status == SendAttemptStatus.UNKNOWN.value
        assert replay.idempotent_replay is True
        assert len(telegram.calls) == 1


@pytest.mark.anyio
async def test_draft_writer_cannot_deadlock_or_reopen_completed_outcome() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        now = datetime.now(UTC)
        attempt_id = uuid4()
        async with sessions() as session:
            session.add(
                SendAttempt(
                    id=attempt_id,
                    reply_draft_id=seed.draft_ids[0],
                    reply_task_id=seed.task_id,
                    conversation_id=seed.conversation_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    principal_subject="reply-draft-lock-order-test",
                    status=SendAttemptStatus.SENDING.value,
                    account_label="RU",
                    target_chat_id=seed.recipient_chat_id,
                    business_connection_id=seed.business_connection_id,
                    reserved_at=now,
                    started_at=now,
                )
            )
            await session.commit()

        writer_task: asyncio.Task[Any] | None = None
        async with sessions() as outcome, sessions() as writer_session:
            await outcome.execute(text("SET LOCAL lock_timeout = '750ms'"))
            locked_attempt = (
                await outcome.execute(
                    select(SendAttempt).where(SendAttempt.id == attempt_id).with_for_update()
                )
            ).scalar_one()
            locked_conversation = (
                await outcome.execute(
                    select(Conversation)
                    .where(Conversation.id == seed.conversation_id)
                    .with_for_update()
                )
            ).scalar_one()

            backend_pid = await writer_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(backend_pid, int)
            writer_task = asyncio.create_task(
                SaveReplyDraftService(SqlReplyDraftWriter(writer_session)).execute(
                    _save_draft_command(seed, "This draft must not be persisted")
                )
            )

            try:
                await _wait_for_backend_lock(
                    sessions,
                    backend_pid=backend_pid,
                )
                locked_task = (
                    await outcome.execute(
                        select(ReplyTask).where(ReplyTask.id == seed.task_id).with_for_update()
                    )
                ).scalar_one()
                locked_draft = (
                    await outcome.execute(
                        select(ReplyDraft)
                        .where(ReplyDraft.id == seed.draft_ids[0])
                        .with_for_update()
                    )
                ).scalar_one()

                locked_attempt.status = SendAttemptStatus.SENT.value
                locked_attempt.provider_message_id = "lock-order-confirmed"
                locked_attempt.finished_at = now
                locked_task.status = ReplyTaskStatus.REPLIED.value
                locked_task.due_at = None
                locked_draft.status = ReplyDraftStatus.APPROVED.value
                locked_conversation.status = ConversationStatus.REPLIED.value
                locked_conversation.requires_reply = False
                await outcome.commit()

                with pytest.raises(ReplyTaskClosedError):
                    await asyncio.wait_for(
                        asyncio.shield(writer_task),
                        timeout=5,
                    )
                await writer_session.rollback()
            finally:
                if outcome.in_transaction():
                    await outcome.rollback()
                if writer_task is not None and not writer_task.done():
                    writer_task.cancel()
                if writer_task is not None:
                    await asyncio.gather(writer_task, return_exceptions=True)
                if writer_session.in_transaction():
                    await writer_session.rollback()

        async with sessions() as observer:
            attempt = await observer.get(SendAttempt, attempt_id)
            task = await observer.get(ReplyTask, seed.task_id)
            draft = await observer.get(ReplyDraft, seed.draft_ids[0])
            conversation = await observer.get(Conversation, seed.conversation_id)
            draft_count = await observer.scalar(
                select(func.count())
                .select_from(ReplyDraft)
                .where(ReplyDraft.reply_task_id == seed.task_id)
            )
            audit_count = await observer.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "reply_draft_saved_from_mcp")
            )
            outbound_count = await observer.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert attempt is not None
        assert attempt.status == SendAttemptStatus.SENT.value
        assert attempt.provider_message_id == "lock-order-confirmed"
        assert task is not None
        assert task.status == ReplyTaskStatus.REPLIED.value
        assert draft is not None
        assert draft.status == ReplyDraftStatus.APPROVED.value
        assert conversation is not None
        assert conversation.status == ConversationStatus.REPLIED.value
        assert conversation.requires_reply is False
        assert draft_count == 1
        assert audit_count == 0
        assert outbound_count == 0


class _LockObservingTelegram:
    def __init__(
        self,
        *,
        sender_session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        draft_id: UUID,
    ) -> None:
        self._sender_session = sender_session
        self._sessions = sessions
        self._draft_id = draft_id
        self.transaction_states: list[bool] = []
        self.committed_attempt_ids: list[UUID] = []
        self.probe_calls = 0
        self.send_calls = 0

    async def _take_independent_draft_lock(self) -> None:
        self.transaction_states.append(self._sender_session.in_transaction())
        async with self._sessions() as observer:
            await observer.execute(text("SET LOCAL lock_timeout = '750ms'"))
            await observer.execute(
                select(ReplyDraft.id).where(ReplyDraft.id == self._draft_id).with_for_update()
            )
            await observer.rollback()

    async def _observe_committed_reservation(self) -> None:
        async with self._sessions() as observer:
            attempt = (
                await observer.execute(
                    select(SendAttempt).where(SendAttempt.reply_draft_id == self._draft_id)
                )
            ).scalar_one()
        assert attempt.status == SendAttemptStatus.SENDING.value
        self.committed_attempt_ids.append(attempt.id)

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probe_calls += 1
        await self._take_independent_draft_lock()
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **_kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        await self._observe_committed_reservation()
        await self._take_independent_draft_lock()
        return {"ok": True, "result": {"message_id": 701}}


@pytest.mark.anyio
async def test_provider_io_runs_without_sender_transaction_or_reply_locks() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as sender_session:
            telegram = _LockObservingTelegram(
                sender_session=sender_session,
                sessions=sessions,
                draft_id=seed.draft_ids[0],
            )

            result = await SqlTelegramReplySender(
                session=sender_session,
                telegram=telegram,
                settings=_settings(),
            ).send(_command(seed, seed.draft_ids[0]))

        assert result.sent is True
        assert telegram.probe_calls == 1
        assert telegram.send_calls == 1
        assert telegram.transaction_states == [False, False]
        assert telegram.committed_attempt_ids == [result.attempt_id]


class _ConcurrentTelegram:
    def __init__(self) -> None:
        self.probe_calls = 0
        self.send_calls = 0
        self.all_probes_started = asyncio.Event()
        self.release_probes = asyncio.Event()
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probe_calls += 1
        if self.probe_calls == 2:
            self.all_probes_started.set()
        await self.release_probes.wait()
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **_kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        self.send_started.set()
        await self.release_send.wait()
        return {"ok": True, "result": {"message_id": 702}}


@pytest.mark.anyio
async def test_two_drafts_for_same_task_reserve_and_deliver_only_once() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        telegram = _ConcurrentTelegram()
        sends = [
            asyncio.create_task(
                _send_once(
                    sessions,
                    seed=seed,
                    draft_id=draft_id,
                    telegram=telegram,
                )
            )
            for draft_id in seed.draft_ids
        ]

        try:
            await asyncio.wait_for(telegram.all_probes_started.wait(), timeout=5)
            telegram.release_probes.set()
            await asyncio.wait_for(telegram.send_started.wait(), timeout=5)

            completed, _pending = await asyncio.wait(
                sends,
                timeout=5,
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert len(completed) == 1
            blocked_error = next(iter(completed)).exception()
            assert isinstance(blocked_error, ReplySendError)
            assert blocked_error.code == "context_changed"
        finally:
            telegram.release_probes.set()
            telegram.release_send.set()

        outcomes = await asyncio.gather(*sends, return_exceptions=True)
        successes = [outcome for outcome in outcomes if isinstance(outcome, ReplySendResult)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, ReplySendError)]

        assert len(successes) == 1
        assert successes[0].sent is True
        assert successes[0].idempotent_replay is False
        assert len(failures) == 1
        assert failures[0].code == "context_changed"
        assert telegram.probe_calls == 2
        assert telegram.send_calls == 1

        async with sessions() as session:
            attempt_count = await session.scalar(select(func.count()).select_from(SendAttempt))
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert attempt_count == 1
        assert outbound_count == 1


@pytest.mark.anyio
async def test_two_tasks_in_same_conversation_reserve_and_deliver_only_once() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        second_task_id, second_draft_id = await _add_reply_task_and_review_current_dialog(
            sessions,
            seed=seed,
        )
        telegram = _ConcurrentTelegram()
        draft_ids = (seed.draft_ids[0], second_draft_id)
        sends = [
            asyncio.create_task(
                _send_once(
                    sessions,
                    seed=seed,
                    draft_id=draft_id,
                    telegram=telegram,
                )
            )
            for draft_id in draft_ids
        ]
        outcomes: list[ReplySendResult | BaseException] = []
        pending_after_release: set[asyncio.Task[ReplySendResult]] = set()

        try:
            await asyncio.wait_for(telegram.all_probes_started.wait(), timeout=5)
            telegram.release_probes.set()
            await asyncio.wait_for(telegram.send_started.wait(), timeout=5)

            async with sessions() as observer:
                reserved_attempts = (await observer.execute(select(SendAttempt))).scalars().all()
            assert len(reserved_attempts) == 1
            assert reserved_attempts[0].status == SendAttemptStatus.SENDING.value
            assert reserved_attempts[0].reply_task_id in {
                seed.task_id,
                second_task_id,
            }

            completed, _pending = await asyncio.wait(
                sends,
                timeout=5,
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert len(completed) == 1
            blocked_error = next(iter(completed)).exception()
            assert isinstance(blocked_error, ReplySendError)
            assert blocked_error.code == "context_changed"
        finally:
            telegram.release_probes.set()
            telegram.release_send.set()
            _completed_after_release, pending_after_release = await asyncio.wait(
                sends,
                timeout=5,
            )
            for pending_send in pending_after_release:
                pending_send.cancel()
            outcomes = await asyncio.gather(*sends, return_exceptions=True)

        assert not pending_after_release, (
            "concurrent reply sends did not stop after provider gates were released"
        )
        successes = [outcome for outcome in outcomes if isinstance(outcome, ReplySendResult)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, ReplySendError)]

        assert len(successes) == 1
        assert successes[0].sent is True
        assert successes[0].idempotent_replay is False
        assert len(failures) == 1
        assert failures[0].code == "context_changed"
        assert telegram.probe_calls == 2
        assert telegram.send_calls == 1

        async with sessions() as session:
            attempt_count = await session.scalar(select(func.count()).select_from(SendAttempt))
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert attempt_count == 1
        assert outbound_count == 1


class _BlockingProbeTelegram:
    def __init__(self) -> None:
        self.probe_started = asyncio.Event()
        self.release_probe = asyncio.Event()
        self.send_calls = 0

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probe_started.set()
        await self.release_probe.wait()
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **_kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        return {"ok": True, "result": {"message_id": 703}}


@pytest.mark.anyio
async def test_draft_edit_during_probe_fails_closed_before_reservation() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingProbeTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )

        try:
            await asyncio.wait_for(telegram.probe_started.wait(), timeout=5)

            async def edit_draft() -> None:
                async with sessions() as session:
                    await session.execute(
                        update(ReplyDraft)
                        .where(ReplyDraft.id == seed.draft_ids[0])
                        .values(
                            draft_text="Changed while Telegram validation was pending",
                            status=ReplyDraftStatus.EDITED.value,
                        )
                    )
                    await session.commit()

            await asyncio.wait_for(edit_draft(), timeout=5)
            telegram.release_probe.set()
            with pytest.raises(ReplySendError) as exc_info:
                await asyncio.wait_for(send, timeout=5)
            assert exc_info.value.code == "send_context_changed"
        finally:
            telegram.release_probe.set()
            if not send.done():
                send.cancel()
                with suppress(asyncio.CancelledError, ReplySendError):
                    await send

        assert telegram.send_calls == 0
        async with sessions() as session:
            attempt_count = await session.scalar(select(func.count()).select_from(SendAttempt))
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert attempt_count == 0
        assert outbound_count == 0


@pytest.mark.anyio
async def test_reservation_replay_does_not_wait_on_outcome_attempt_lock() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingProbeTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )
        attempt_id = uuid4()

        try:
            # Reaching the probe proves preflight observed no attempt. The sender
            # must therefore enter the authoritative reservation/context-lock path
            # after the independently committed attempt appears below.
            await asyncio.wait_for(telegram.probe_started.wait(), timeout=5)
            now = datetime.now(UTC)
            async with sessions() as session:
                session.add(
                    SendAttempt(
                        id=attempt_id,
                        reply_draft_id=seed.draft_ids[0],
                        reply_task_id=seed.task_id,
                        conversation_id=seed.conversation_id,
                        account_id=seed.account_id,
                        project_id=seed.project_id,
                        principal_subject="concurrent-outcome",
                        status=SendAttemptStatus.SENDING.value,
                        account_label="RU",
                        target_chat_id=seed.recipient_chat_id,
                        business_connection_id=seed.business_connection_id,
                        reserved_at=now,
                        started_at=now,
                    )
                )
                await session.commit()

            async with sessions() as outcome_session:
                await outcome_session.execute(text("SET LOCAL lock_timeout = '750ms'"))
                locked_attempt = (
                    await outcome_session.execute(
                        select(SendAttempt).where(SendAttempt.id == attempt_id).with_for_update()
                    )
                ).scalar_one()
                telegram.release_probe.set()
                # This must finish while outcome_session still holds the attempt
                # row lock. A context -> attempt FOR UPDATE inversion times out here.
                replay = await asyncio.wait_for(
                    asyncio.shield(send),
                    timeout=5,
                )
                assert outcome_session.in_transaction() is True

                # Outcome's shared suffix is conversation -> tasks -> draft.
                # Reservation never locks the attempt, so attempt -> conversation
                # cannot form a cycle with a replay waiting at the conversation fence.
                await outcome_session.execute(
                    select(Conversation.id)
                    .where(Conversation.id == seed.conversation_id)
                    .with_for_update()
                )
                await outcome_session.execute(
                    select(ReplyTask.id).where(ReplyTask.id == seed.task_id).with_for_update()
                )
                await outcome_session.execute(
                    select(ReplyDraft.id)
                    .where(ReplyDraft.id == seed.draft_ids[0])
                    .with_for_update()
                )
                locked_attempt.status = SendAttemptStatus.SENT.value
                locked_attempt.provider_message_id = "lock-order-outcome"
                locked_attempt.finished_at = datetime.now(UTC)
                await outcome_session.commit()
        finally:
            telegram.release_probe.set()
            if not send.done():
                send.cancel()
                with suppress(asyncio.CancelledError, ReplySendError):
                    await send

        assert replay.attempt_id == attempt_id
        assert replay.status == SendAttemptStatus.SENDING.value
        assert replay.sent is False
        assert replay.idempotent_replay is True
        assert telegram.send_calls == 0

        async with sessions() as session:
            persisted_attempt = await session.get(SendAttempt, attempt_id)
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert persisted_attempt is not None
        assert persisted_attempt.status == SendAttemptStatus.SENT.value
        assert persisted_attempt.provider_message_id == "lock-order-outcome"
        assert outbound_count == 0


class _ReplacementConnectionTelegram:
    def __init__(self, *, origin_id: str, replacement_id: str) -> None:
        self._origin_id = origin_id
        self._replacement_id = replacement_id
        self.probed_connection_ids: list[str] = []
        self.sent_business_connection_id: object | None = None
        self.send_calls = 0

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probed_connection_ids.append(business_connection_id)
        if business_connection_id == self._origin_id:
            raise TelegramApiError(
                error_code=400,
                description="BUSINESS_CONNECTION_INVALID",
                http_status=400,
            )
        assert business_connection_id == self._replacement_id
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        self.sent_business_connection_id = kwargs.get("business_connection_id")
        return {"ok": True, "result": {"message_id": 706}}


@pytest.mark.anyio
async def test_stable_same_account_replacement_connection_can_deliver() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        replacement_id = f"bc-replacement-{uuid4().hex}"
        replacement_row_id = uuid4()
        now = datetime.now(UTC)
        async with sessions() as session:
            session.add(
                TelegramBusinessConnection(
                    id=replacement_row_id,
                    account_id=seed.account_id,
                    business_connection_id=replacement_id,
                    telegram_user_id=999,
                    user_chat_id=999,
                    rights={"can_reply": True},
                    is_enabled=False,
                    received_at=now,
                    last_update_at=now,
                )
            )
            await session.commit()

        telegram = _ReplacementConnectionTelegram(
            origin_id=seed.business_connection_id,
            replacement_id=replacement_id,
        )
        result = await asyncio.wait_for(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            ),
            timeout=5,
        )

        assert result.sent is True
        assert result.idempotent_replay is False
        assert telegram.probed_connection_ids == [
            seed.business_connection_id,
            replacement_id,
        ]
        assert telegram.send_calls == 1
        assert telegram.sent_business_connection_id == replacement_id

        async with sessions() as session:
            connections = (
                await session.execute(
                    select(TelegramBusinessConnection).where(
                        TelegramBusinessConnection.account_id == seed.account_id
                    )
                )
            ).scalars()
            by_connection_id = {
                connection.business_connection_id: connection for connection in connections.all()
            }
            attempt = (await session.execute(select(SendAttempt))).scalar_one()

        assert by_connection_id[seed.business_connection_id].is_enabled is False
        assert by_connection_id[replacement_id].is_enabled is True
        assert attempt.status == SendAttemptStatus.SENT.value
        assert attempt.business_connection_id == replacement_id


async def _rotate_business_connection(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: _SeededReply,
    replacement_row_id: UUID,
    replacement_id: str,
    rotated_at: datetime,
) -> None:
    async with sessions() as session:
        await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
        original = (
            await session.execute(
                select(TelegramBusinessConnection)
                .where(
                    TelegramBusinessConnection.account_id == seed.account_id,
                    TelegramBusinessConnection.business_connection_id
                    == seed.business_connection_id,
                )
                .with_for_update()
            )
        ).scalar_one()
        original.is_enabled = False
        original.rights = {"can_reply": False, "can_read_messages": True}
        original.last_error = "rotated_by_operator"
        original.last_update_at = rotated_at
        await session.flush()
        session.add(
            TelegramBusinessConnection(
                id=replacement_row_id,
                account_id=seed.account_id,
                business_connection_id=replacement_id,
                telegram_user_id=999,
                user_chat_id=999,
                rights={"can_reply": True, "can_read_messages": True},
                is_enabled=True,
                received_at=rotated_at,
                last_update_at=rotated_at,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_connection_rotation_during_probe_fails_closed_without_overwrite() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingProbeTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )
        replacement_row_id = uuid4()
        replacement_id = f"bc-rotated-{uuid4().hex}"
        rotated_at = datetime.now(UTC) + timedelta(seconds=1)

        try:
            await asyncio.wait_for(telegram.probe_started.wait(), timeout=5)
            await asyncio.wait_for(
                _rotate_business_connection(
                    sessions,
                    seed=seed,
                    replacement_row_id=replacement_row_id,
                    replacement_id=replacement_id,
                    rotated_at=rotated_at,
                ),
                timeout=5,
            )
            telegram.release_probe.set()
            with pytest.raises(ReplySendError) as exc_info:
                await asyncio.wait_for(send, timeout=5)
            assert exc_info.value.code == "send_context_changed"
        finally:
            telegram.release_probe.set()
            if not send.done():
                send.cancel()
                with suppress(asyncio.CancelledError, ReplySendError):
                    await send

        assert telegram.send_calls == 0
        async with sessions() as session:
            original = (
                await session.execute(
                    select(TelegramBusinessConnection).where(
                        TelegramBusinessConnection.business_connection_id
                        == seed.business_connection_id
                    )
                )
            ).scalar_one()
            replacement = await session.get(
                TelegramBusinessConnection,
                replacement_row_id,
            )
            attempt_count = await session.scalar(select(func.count()).select_from(SendAttempt))
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert attempt_count == 0
        assert outbound_count == 0
        assert original.is_enabled is False
        assert original.rights == {
            "can_reply": False,
            "can_read_messages": True,
        }
        assert original.last_error == "rotated_by_operator"
        assert original.last_update_at == rotated_at
        assert replacement is not None
        assert replacement.is_enabled is True
        assert replacement.rights == {
            "can_reply": True,
            "can_read_messages": True,
        }
        assert replacement.last_update_at == rotated_at


class _BlockingSendTelegram:
    def __init__(self) -> None:
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.probe_calls = 0
        self.send_calls = 0

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probe_calls += 1
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **_kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        self.send_started.set()
        await self.release_send.wait()
        return {"ok": True, "result": {"message_id": 704}}


async def _insert_new_inbound(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: _SeededReply,
    message_id: UUID,
    task_id: UUID,
    received_at: datetime,
) -> None:
    async with sessions() as session:
        await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
        conversation = (
            await session.execute(
                select(Conversation)
                .where(Conversation.id == seed.conversation_id)
                .with_for_update()
            )
        ).scalar_one()
        session.add_all(
            [
                Message(
                    id=message_id,
                    bot_id=seed.bot_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    direction=MessageDirection.INBOUND.value,
                    source="telegram",
                    event_type="business_message",
                    chat_id=seed.recipient_chat_id,
                    sender_id=seed.recipient_chat_id,
                    text="A newer inbound message",
                    business_connection_id=seed.business_connection_id,
                    payload={},
                    received_at=received_at,
                ),
                ReplyTask(
                    id=task_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    conversation_id=seed.conversation_id,
                    trigger_message_id=message_id,
                    status=ReplyTaskStatus.NEEDS_REPLY.value,
                    priority=100,
                    reason="new_inbound_while_send_pending",
                ),
            ]
        )
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
        conversation.last_inbound_at = received_at
        conversation.last_message_at = received_at
        await session.commit()


@pytest.mark.anyio
async def test_new_inbound_during_probe_changes_context_before_reservation() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingProbeTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )
        new_message_id = uuid4()
        new_task_id = uuid4()
        new_inbound_at = datetime.now(UTC) + timedelta(seconds=1)

        try:
            await asyncio.wait_for(telegram.probe_started.wait(), timeout=5)
            await asyncio.wait_for(
                _insert_new_inbound(
                    sessions,
                    seed=seed,
                    message_id=new_message_id,
                    task_id=new_task_id,
                    received_at=new_inbound_at,
                ),
                timeout=5,
            )
            telegram.release_probe.set()
            with pytest.raises(ReplySendError) as exc_info:
                await asyncio.wait_for(send, timeout=5)
            assert exc_info.value.code == "context_changed"
        finally:
            telegram.release_probe.set()
            if not send.done():
                send.cancel()
                with suppress(asyncio.CancelledError, ReplySendError):
                    await send

        assert telegram.send_calls == 0
        async with sessions() as session:
            attempt_count = await session.scalar(select(func.count()).select_from(SendAttempt))
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )
            remaining_count = await session.scalar(
                select(func.count())
                .select_from(ReplyTask)
                .where(
                    ReplyTask.conversation_id == seed.conversation_id,
                    ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                )
            )
            new_task = await session.get(ReplyTask, new_task_id)
            conversation = await session.get(Conversation, seed.conversation_id)

        assert attempt_count == 0
        assert outbound_count == 0
        assert remaining_count == 2
        assert new_task is not None
        assert new_task.status == ReplyTaskStatus.NEEDS_REPLY.value
        assert conversation is not None
        assert conversation.status == ConversationStatus.OPEN.value
        assert conversation.requires_reply is True
        assert conversation.last_inbound_at == new_inbound_at
        assert conversation.last_message_at == new_inbound_at


@pytest.mark.anyio
async def test_new_inbound_during_blocked_send_remains_unanswered() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingSendTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )
        new_message_id = uuid4()
        new_task_id = uuid4()

        try:
            await asyncio.wait_for(telegram.send_started.wait(), timeout=5)
            new_inbound_at = datetime.now(UTC) + timedelta(seconds=1)
            await asyncio.wait_for(
                _insert_new_inbound(
                    sessions,
                    seed=seed,
                    message_id=new_message_id,
                    task_id=new_task_id,
                    received_at=new_inbound_at,
                ),
                timeout=5,
            )

            telegram.release_send.set()
            result = await asyncio.wait_for(send, timeout=5)
        finally:
            telegram.release_send.set()
            if not send.done():
                send.cancel()
                with suppress(asyncio.CancelledError):
                    await send

        assert result.sent is True
        assert telegram.send_calls == 1

        async with sessions() as session:
            original_task = await session.get(ReplyTask, seed.task_id)
            new_task = await session.get(ReplyTask, new_task_id)
            conversation = await session.get(Conversation, seed.conversation_id)
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )
            remaining_count = await session.scalar(
                select(func.count())
                .select_from(ReplyTask)
                .where(
                    ReplyTask.conversation_id == seed.conversation_id,
                    ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                )
            )

        assert original_task is not None
        assert original_task.status == ReplyTaskStatus.REPLIED.value
        assert new_task is not None
        assert new_task.status == ReplyTaskStatus.NEEDS_REPLY.value
        assert conversation is not None
        assert conversation.status == ConversationStatus.OPEN.value
        assert conversation.requires_reply is True
        assert conversation.last_inbound_at == new_inbound_at
        assert conversation.last_message_at >= new_inbound_at
        assert remaining_count == 1
        assert outbound_count == 1


async def _dismiss_task_and_close_conversation(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: _SeededReply,
    conversation_status: str,
) -> None:
    async with sessions() as session:
        await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
        conversation = (
            await session.execute(
                select(Conversation)
                .where(Conversation.id == seed.conversation_id)
                .with_for_update()
            )
        ).scalar_one()
        task = (
            await session.execute(
                select(ReplyTask).where(ReplyTask.id == seed.task_id).with_for_update()
            )
        ).scalar_one()
        task.status = ReplyTaskStatus.DISMISSED.value
        conversation.status = conversation_status
        conversation.requires_reply = False
        await session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_status",
    [ConversationStatus.CLOSED.value, ConversationStatus.MUTED.value],
)
async def test_human_terminal_state_during_blocked_send_is_preserved(
    terminal_status: str,
) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingSendTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )

        try:
            await asyncio.wait_for(telegram.send_started.wait(), timeout=5)
            await asyncio.wait_for(
                _dismiss_task_and_close_conversation(
                    sessions,
                    seed=seed,
                    conversation_status=terminal_status,
                ),
                timeout=5,
            )
            telegram.release_send.set()
            result = await asyncio.wait_for(send, timeout=5)
        finally:
            telegram.release_send.set()
            if not send.done():
                send.cancel()
                with suppress(asyncio.CancelledError):
                    await send

        assert result.sent is True
        assert result.status == SendAttemptStatus.SENT.value
        assert result.idempotent_replay is False
        assert telegram.probe_calls == 1
        assert telegram.send_calls == 1

        async with sessions() as session:
            attempts = (await session.execute(select(SendAttempt))).scalars().all()
            task = await session.get(ReplyTask, seed.task_id)
            conversation = await session.get(Conversation, seed.conversation_id)
            outbound_messages = (
                (
                    await session.execute(
                        select(Message).where(Message.source == "gate-approved-draft")
                    )
                )
                .scalars()
                .all()
            )

        assert len(attempts) == 1
        assert attempts[0].id == result.attempt_id
        assert attempts[0].status == SendAttemptStatus.SENT.value
        assert attempts[0].provider_message_id == "704"
        assert attempts[0].finished_at is not None
        assert task is not None
        assert task.status == ReplyTaskStatus.DISMISSED.value
        assert conversation is not None
        assert conversation.status == terminal_status
        assert conversation.requires_reply is False
        assert conversation.last_outbound_at is not None
        assert conversation.last_message_at >= conversation.last_outbound_at
        assert len(outbound_messages) == 1
        assert outbound_messages[0].sent_at == attempts[0].finished_at
        assert outbound_messages[0].sent_at == conversation.last_outbound_at


class _TimeoutTelegram:
    def __init__(self) -> None:
        self.probe_calls = 0
        self.send_calls = 0

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probe_calls += 1
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **_kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        request = httpx.Request("POST", "https://api.telegram.org/sendMessage")
        raise httpx.ReadTimeout("delivery outcome unknown", request=request)


@pytest.mark.anyio
async def test_timeout_is_unknown_and_exact_retry_replays_without_provider_io() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _TimeoutTelegram()

        first = await _send_once(
            sessions,
            seed=seed,
            draft_id=seed.draft_ids[0],
            telegram=telegram,
        )
        replay = await _send_once(
            sessions,
            seed=seed,
            draft_id=seed.draft_ids[0],
            telegram=telegram,
        )

        assert first.status == SendAttemptStatus.UNKNOWN.value
        assert first.sent is False
        assert first.idempotent_replay is False
        assert first.error_code == "telegram_timeout"
        assert replay.idempotent_replay is True
        assert replace(replay, idempotent_replay=False) == first
        assert telegram.probe_calls == 1
        assert telegram.send_calls == 1

        async with sessions() as session:
            attempts = (await session.execute(select(SendAttempt))).scalars().all()
            task = await session.get(ReplyTask, seed.task_id)
            draft = await session.get(ReplyDraft, seed.draft_ids[0])
            conversation = await session.get(Conversation, seed.conversation_id)
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert len(attempts) == 1
        assert attempts[0].id == first.attempt_id
        assert attempts[0].status == SendAttemptStatus.UNKNOWN.value
        assert task is not None
        assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
        assert draft is not None
        assert draft.status == ReplyDraftStatus.GENERATED.value
        assert conversation is not None
        assert conversation.status == ConversationStatus.OPEN.value
        assert conversation.requires_reply is True
        assert outbound_count == 0


class _ImmediateTelegram:
    def __init__(self) -> None:
        self.probe_calls = 0
        self.send_calls = 0

    async def get_business_connection(
        self,
        *,
        business_connection_id: str,
    ) -> dict[str, object]:
        self.probe_calls += 1
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **_kwargs: object) -> dict[str, object]:
        self.send_calls += 1
        return {"ok": True, "result": {"message_id": 705}}


@pytest.mark.anyio
async def test_outcome_flush_failure_rolls_back_around_committed_sending_fence() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _ImmediateTelegram()

        def fail_outbound_flush(
            sync_session: Any,
            _flush_context: Any,
            _instances: Any,
        ) -> None:
            if any(
                isinstance(instance, Message) and instance.source == "gate-approved-draft"
                for instance in sync_session.new
            ):
                raise RuntimeError("injected reply outcome flush failure")

        async with sessions() as sender_session:
            event.listen(sender_session.sync_session, "before_flush", fail_outbound_flush)
            try:
                with pytest.raises(
                    RuntimeError,
                    match="injected reply outcome flush failure",
                ):
                    await SqlTelegramReplySender(
                        session=sender_session,
                        telegram=telegram,
                        settings=_settings(),
                    ).send(_command(seed, seed.draft_ids[0]))
            finally:
                event.remove(
                    sender_session.sync_session,
                    "before_flush",
                    fail_outbound_flush,
                )
                await sender_session.rollback()

        async with sessions() as session:
            committed = (
                await session.execute(
                    select(SendAttempt).where(SendAttempt.reply_draft_id == seed.draft_ids[0])
                )
            ).scalar_one()
            committed_attempt_id = committed.id
            assert committed.status == SendAttemptStatus.SENDING.value

        replay = await _send_once(
            sessions,
            seed=seed,
            draft_id=seed.draft_ids[0],
            telegram=telegram,
        )

        assert replay.attempt_id == committed_attempt_id
        assert replay.status == SendAttemptStatus.SENDING.value
        assert replay.sent is False
        assert replay.idempotent_replay is True
        assert telegram.probe_calls == 1
        assert telegram.send_calls == 1

        async with sessions() as session:
            attempts = (await session.execute(select(SendAttempt))).scalars().all()
            task = await session.get(ReplyTask, seed.task_id)
            draft = await session.get(ReplyDraft, seed.draft_ids[0])
            conversation = await session.get(Conversation, seed.conversation_id)
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert len(attempts) == 1
        assert attempts[0].id == committed_attempt_id
        assert attempts[0].status == SendAttemptStatus.SENDING.value
        assert task is not None
        assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
        assert draft is not None
        assert draft.status == ReplyDraftStatus.GENERATED.value
        assert conversation is not None
        assert conversation.status == ConversationStatus.OPEN.value
        assert conversation.requires_reply is True
        assert outbound_count == 0


@pytest.mark.anyio
async def test_cancelled_post_leaves_committed_sending_fence_for_exact_retry() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _BlockingSendTelegram()
        send = asyncio.create_task(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            )
        )

        await asyncio.wait_for(telegram.send_started.wait(), timeout=5)
        async with sessions() as observer:
            committed = (
                await observer.execute(
                    select(SendAttempt).where(SendAttempt.reply_draft_id == seed.draft_ids[0])
                )
            ).scalar_one()
            committed_attempt_id = committed.id
            assert committed.status == SendAttemptStatus.SENDING.value

        send.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send
        telegram.release_send.set()

        replay = await asyncio.wait_for(
            _send_once(
                sessions,
                seed=seed,
                draft_id=seed.draft_ids[0],
                telegram=telegram,
            ),
            timeout=5,
        )

        assert replay.attempt_id == committed_attempt_id
        assert replay.status == SendAttemptStatus.SENDING.value
        assert replay.sent is False
        assert replay.idempotent_replay is True
        assert replay.provider_message_id is None
        assert replay.error_code is None
        assert telegram.probe_calls == 1
        assert telegram.send_calls == 1

        async with sessions() as session:
            attempts = (await session.execute(select(SendAttempt))).scalars().all()
            task = await session.get(ReplyTask, seed.task_id)
            draft = await session.get(ReplyDraft, seed.draft_ids[0])
            conversation = await session.get(Conversation, seed.conversation_id)
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.source == "gate-approved-draft")
            )

        assert len(attempts) == 1
        assert attempts[0].id == committed_attempt_id
        assert attempts[0].status == SendAttemptStatus.SENDING.value
        assert task is not None
        assert task.status == ReplyTaskStatus.NEEDS_REPLY.value
        assert draft is not None
        assert draft.status == ReplyDraftStatus.GENERATED.value
        assert conversation is not None
        assert conversation.status == ConversationStatus.OPEN.value
        assert conversation.requires_reply is True
        assert outbound_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize("extension", [".xlsx", ".docx", ".pptx", ".xls", ".doc", ".ppt",
                                      ".jpg", ".png", ".webp", ".gif"])
async def test_original_document_formats_replay_exact_bytes(extension: str) -> None:
    from app.application.document_formats import DOCUMENT_TYPES

    from test_document_formats import image_bytes, legacy_office, ooxml

    data = (ooxml(extension) if extension.endswith("x") else legacy_office(extension)
            if extension in {".xls", ".doc", ".ppt"} else image_bytes(extension))
    filename = "Gate-original" + extension
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await _attach_document(sessions, seed, filename=filename,
                               media_type=DOCUMENT_TYPES[extension], data=data)
        command = replace(_document_command(seed, data), expected_document_filename=filename)
        telegram = _DocumentTelegram()
        results = []
        for _ in range(2):
            async with sessions() as session:
                results.append(await SqlTelegramReplySender(
                    session=session, telegram=telegram,
                    settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
                ).send(command))
        assert results[0].sent and results[1].idempotent_replay
        assert results[0].attempt_id == results[1].attempt_id
        assert len(telegram.calls) == 1
        assert telegram.calls[0]["data"] == data
        assert telegram.calls[0]["filename"] == filename
        assert telegram.calls[0]["media_type"] == DOCUMENT_TYPES[extension]


@pytest.mark.anyio
async def test_document_mime_substitution_is_rejected_before_provider_io() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        data = await _attach_document(sessions, seed)
        async with sessions() as session:
            await session.execute(update(ReplyDocument).where(
                ReplyDocument.reply_draft_id == seed.draft_ids[0]
            ).values(media_type="image/png"))
            await session.commit()
        telegram = _DocumentTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError, match="expired or changed"):
                await SqlTelegramReplySender(
                    session=session, telegram=telegram,
                    settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
                ).send(_document_command(seed, data))
        assert telegram.calls == []

