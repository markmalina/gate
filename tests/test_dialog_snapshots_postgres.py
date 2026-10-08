"""Actual durable-ingress/send races in isolated PostgreSQL; no Telegram I/O."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.api.mcp_routes import _send_approved_reply_tool_result
from app.application.mcp_inbox import get_reply_context
from app.application.reply_drafts import ReplyDraftError, SaveReplyDraftService
from app.application.reply_sends import ReplySendError
from app.application.telegram_processing import AcceptTelegramReceiptCommand
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.dialog_snapshots import dialog_snapshot
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import (
    Conversation,
    Message,
    Project,
    ReplyDialogVersion,
    ReplyDraft,
    SendAttempt,
    TelegramBusinessConnection,
)
from app.worker.reply_sequences import run_reply_sequence_once
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_reply_sends_postgres import (
    _attach_document,
    _command,
    _document_command,
    _DocumentTelegram,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
    _sequence_command,
    _settings,
    _wait_for_backend_lock,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def receive(sessions: Any, seed: Any, *, update_id: int = 1,
                  owner: bool = False, content: dict | None = None,
                  kind: str = "business_message", chat_id: str | None = None) -> None:
    async with sessions() as session:
        await SqlTelegramReceiptStore(session).create_if_absent(AcceptTelegramReceiptCommand(
            bot_key=TelegramConnectorKey.BUSINESS.value, update_id=update_id,
            payload={"update_id": update_id, kind: {
                "business_connection_id": seed.business_connection_id,
                "chat": {"id": int(chat_id or seed.recipient_chat_id)},
                "from": {"id": 999 if owner else int(seed.recipient_chat_id)},
                "message_id": update_id,
                **({"text": "New synthetic message"} if content is None else content),
            }}, received_at=datetime.now(UTC),
        ))


@pytest.mark.anyio
async def test_unchanged_snapshot_sends_once_and_replays_after_dialog_changes() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _LiteralRecordingTelegram()
        command = _command(seed, seed.draft_ids[0])
        async with sessions() as session:
            first = await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(command)
        await receive(sessions, seed)
        async with sessions() as session:
            replay = await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(command)
        assert first.sent and replay.sent and replay.idempotent_replay
        assert len(telegram.plain_calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("content", [
    {"text": "угу"}, {"voice": {"file_id": "synthetic"}},
    {"photo": []}, {"document": {"file_name": "synthetic.xlsx"}},
])
async def test_raw_receipt_stops_stale_draft_without_waiting_for_projection(
    owner: bool, content: dict,
) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await receive(sessions, seed, owner=owner, content=content)
        telegram = _LiteralRecordingTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError, match="диалог изменился") as error:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert error.value.code == "context_changed"
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
        assert telegram.plain_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("extension", [".xlsx", ".png"])
async def test_stale_original_office_or_image_document_never_dispatches(extension: str) -> None:
    from app.application.document_formats import DOCUMENT_TYPES

    from test_document_formats import image_bytes, ooxml

    data = ooxml(extension) if extension == ".xlsx" else image_bytes(extension)
    filename = "synthetic" + extension
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await _attach_document(
            sessions, seed, filename=filename, media_type=DOCUMENT_TYPES[extension], data=data,
        )
        await receive(sessions, seed)
        telegram = _DocumentTelegram()
        settings = _settings()
        settings.gate_document_send_enabled = True
        async with sessions() as session:
            with pytest.raises(ReplySendError) as error:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=settings,
                ).send(replace(_document_command(seed, data), expected_document_filename=filename))
            assert error.value.code == "context_changed"
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
        assert telegram.calls == []


@pytest.mark.anyio
async def test_raw_event_during_sequence_http_stops_the_unsent_tail() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        now = datetime.now(UTC)
        async with sessions() as session:
            await SqlReplySequenceWriter(session, now=now - timedelta(hours=1)).queue(
                _sequence_command(seed, seed.draft_ids),
            )
            await session.commit()

        class Telegram(_LiteralRecordingTelegram):
            async def send_plain_message(self, **kwargs: Any) -> dict:
                await receive(sessions, seed)
                return await super().send_plain_message(**kwargs)

        telegram = Telegram()
        assert await run_reply_sequence_once(
            settings=_settings(), session_factory=sessions, telegram=telegram, now=now,
        ) == 1
        assert await run_reply_sequence_once(
            settings=_settings(), session_factory=sessions, telegram=telegram,
            now=now + timedelta(minutes=2),
        ) == 0
        assert len(telegram.plain_calls) == 1
        async with sessions() as session:
            tail = await session.get(ReplyDraft, seed.draft_ids[1])
            assert tail.prompt_context["reply_sequence"]["state"] == "stopped"
            assert tail.prompt_context["reply_sequence"]["error_code"] == "context_changed"


@pytest.mark.anyio
async def test_refusal_does_not_grant_context_read_permission() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await receive(sessions, seed)
        command = _command(seed, seed.draft_ids[0])
        async with sessions() as session:
            response = await _send_approved_reply_tool_result(
                request_id=1, arguments={
                    "draft_id": str(command.draft_id), "confirmed": True,
                    "expected_account": command.expected_account,
                    "expected_recipient_chat_id": command.expected_recipient_chat_id,
                }, principal=command.principal, session=session,
                telegram=_LiteralRecordingTelegram(), settings=_settings(),
            )
        assert response["result"]["structuredContent"] == {
            "error": "context_changed", "sent": False, "current_context": None,
        }


@pytest.mark.anyio
async def test_other_project_change_invalidates_without_exposing_its_content() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        project_id = uuid4()
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope
        async with sessions() as session:
            session.add(Project(
                id=project_id, alias="synthetic-private-" + uuid4().hex,
                name="Synthetic other project", adapter_type="test",
            ))
            await session.flush()
            session.add_all([
                Conversation(
                    bot_id=seed.bot_id, account_id=seed.account_id, project_id=project_id,
                    chat_id=seed.recipient_chat_id,
                    business_connection_id=seed.business_connection_id,
                    last_message_at=datetime.now(UTC),
                ),
                Message(
                    bot_id=seed.bot_id, account_id=seed.account_id, project_id=project_id,
                    chat_id=seed.recipient_chat_id, direction="inbound", source="synthetic",
                    sender_id=seed.recipient_chat_id,
                    event_type="business_message", text="Synthetic restricted message", payload={},
                ),
            ])
            await session.commit()
            conversation = await session.get(Conversation, seed.conversation_id)
            assert await dialog_snapshot(
                session, scope=scope, conversation=conversation,
            ) != seed.context_snapshot
        await receive(sessions, seed, content={"text": "Synthetic restricted raw event"})
        async with sessions() as session:
            context = await get_reply_context(
                session=session, scope=scope, task_id=seed.task_id, limit=30,
            )
        assert context["dialog_events"][-1]["text"] is None
        assert context["dialog_events"][-1]["content_available"] is False
        assert context["context_ready"] is False and context["context_snapshot"] is None
        assert "Synthetic restricted" not in str(context)


@pytest.mark.anyio
async def test_history_without_connection_is_readable_but_never_authorizes_delivery() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        command = _command(seed, seed.draft_ids[0])
        async with sessions() as session:
            await session.execute(delete(TelegramBusinessConnection))
            conversation = await session.get(Conversation, seed.conversation_id)
            trigger = await session.get(Message, seed.trigger_message_id)
            conversation.business_connection_id = None
            trigger.business_connection_id = None
            await session.commit()
            context = await get_reply_context(
                session=session, scope=command.principal.access_scope,
                task_id=seed.task_id, limit=30,
            )
            assert context["messages"] and context["context_snapshot"] is not None
            await session.rollback()
            telegram = _LiteralRecordingTelegram()
            with pytest.raises(ReplySendError) as error:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=_settings(),
                ).send(command)
            assert error.value.code == "origin_connection_missing"
            assert telegram.plain_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["edited_business_message", "deleted_business_messages"])
async def test_edit_or_delete_after_review_also_refuses(kind: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await receive(sessions, seed, kind=kind)
        async with sessions() as session:
            with pytest.raises(ReplySendError) as error:
                await SqlTelegramReplySender(
                    session=session, telegram=_LiteralRecordingTelegram(), settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert error.value.code == "context_changed"


@pytest.mark.anyio
async def test_duplicate_receipt_is_stable_and_other_chat_does_not_block() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await receive(sessions, seed, chat_id="54321")
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ReplyDialogVersion)) == 1
            conversation = await session.get(Conversation, seed.conversation_id)
            assert await dialog_snapshot(
                session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                conversation=conversation,
            ) == seed.context_snapshot
        await receive(sessions, seed, update_id=2)
        async with sessions() as session:
            conversation = await session.get(Conversation, seed.conversation_id)
            token = await dialog_snapshot(
                session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                conversation=conversation,
            )
        await receive(sessions, seed, update_id=2)
        async with sessions() as session:
            conversation = await session.get(Conversation, seed.conversation_id)
            assert await dialog_snapshot(
                session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                conversation=conversation,
            ) == token


@pytest.mark.anyio
async def test_save_requires_the_snapshot_actually_reviewed_by_work() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        for token in (None, "dialog-v1:" + "0" * 64):
            async with sessions() as session:
                with pytest.raises(ReplyDraftError):
                    await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(replace(
                        _save_draft_command(seed, "New reply"), expected_context_snapshot=token,
                    ))
        await receive(sessions, seed)
        async with sessions() as session:
            with pytest.raises(ReplyDraftError) as error:
                await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                    _save_draft_command(seed, "Stale reply"),
                )
            assert error.value.code == "context_changed"


@pytest.mark.anyio
async def test_change_during_connection_probe_is_rechecked_before_reservation() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)

        class Telegram(_LiteralRecordingTelegram):
            async def get_business_connection(self, **kwargs: Any) -> dict:
                await receive(sessions, seed)
                return await super().get_business_connection(**kwargs)

        telegram = Telegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as error:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert error.value.code == "context_changed"
        assert telegram.plain_calls == []


@pytest.mark.anyio
async def test_change_after_reservation_before_dispatch_is_failed_without_http() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _LiteralRecordingTelegram()

        class Sender(SqlTelegramReplySender):
            async def _admit_dispatch(self, **kwargs: Any) -> None:
                await receive(sessions, seed)
                await super()._admit_dispatch(**kwargs)

        command = _command(seed, seed.draft_ids[0])
        async with sessions() as session:
            with pytest.raises(ReplySendError) as error:
                await Sender(session=session, telegram=telegram, settings=_settings()).send(command)
            assert error.value.code == "context_changed"
            attempt = await session.scalar(select(SendAttempt))
            assert attempt.status == "failed" and attempt.error_code == "context_changed"
        async with sessions() as session:
            replay = await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(command)
            assert replay.idempotent_replay and not replay.sent
        assert telegram.plain_calls == []


@pytest.mark.anyio
async def test_mcp_refusal_returns_new_text_direction_and_current_snapshot() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await receive(sessions, seed, owner=True)
        command = _command(seed, seed.draft_ids[0])
        principal = replace(
            command.principal, scopes=command.principal.scopes | {"gate:inbox:read"},
        )
        async with sessions() as session:
            response = await _send_approved_reply_tool_result(
                request_id=1, arguments={
                    "draft_id": str(command.draft_id), "confirmed": True,
                    "expected_account": command.expected_account,
                    "expected_recipient_chat_id": command.expected_recipient_chat_id,
                }, principal=principal, session=session,
                telegram=_LiteralRecordingTelegram(), settings=_settings(),
            )
        result = response["result"]["structuredContent"]
        assert result["error"] == "context_changed" and result["sent"] is False
        current = result["current_context"]
        assert current["context_snapshot"] != seed.context_snapshot
        assert current["dialog_events"][-1]["text"] == "New synthetic message"
        assert current["dialog_events"][-1]["direction"] == "outbound"


@pytest.mark.anyio
async def test_legacy_draft_has_no_automatic_snapshot_backfill() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            draft = await session.get(ReplyDraft, seed.draft_ids[0])
            draft.prompt_context = {}
            await session.commit()
        telegram = _LiteralRecordingTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as error:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert error.value.code == "context_snapshot_required"
        assert telegram.plain_calls == []


@pytest.mark.anyio
async def test_two_works_with_one_review_cannot_both_send() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        telegram = _LiteralRecordingTelegram()

        async def send(draft_id: Any) -> Any:
            async with sessions() as session:
                return await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=_settings(),
                ).send(_command(seed, draft_id))

        outcomes = await asyncio.gather(*(send(draft) for draft in seed.draft_ids),
                                        return_exceptions=True)
        assert sum(not isinstance(outcome, Exception) and outcome.sent
                   for outcome in outcomes) == 1
        assert len(telegram.plain_calls) == 1


@pytest.mark.anyio
async def test_ingress_commit_racing_dispatch_serializes_on_the_actual_version_row() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        committing, release = asyncio.Event(), asyncio.Event()

        class IngressSession(AsyncSession):
            async def commit(self) -> None:
                committing.set()
                await asyncio.wait_for(release.wait(), 10)
                await super().commit()

        async def ingress() -> None:
            async with IngressSession(bind=sessions.kw["bind"], expire_on_commit=False) as session:
                await SqlTelegramReceiptStore(session).create_if_absent(
                    AcceptTelegramReceiptCommand(
                        bot_key=TelegramConnectorKey.BUSINESS.value, update_id=42,
                        received_at=datetime.now(UTC), payload={"update_id": 42,
                            "business_message": {
                                "business_connection_id": seed.business_connection_id,
                                "chat": {"id": int(seed.recipient_chat_id)},
                                "from": {"id": int(seed.recipient_chat_id)},
                                "text": "Race synthetic message",
                            }},
                    )
                )

        receiver = asyncio.create_task(ingress())
        telegram = _LiteralRecordingTelegram()
        await asyncio.wait_for(committing.wait(), 10)
        async with sessions() as session:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
            sender = asyncio.create_task(SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(_command(seed, seed.draft_ids[0])))
            try:
                await _wait_for_backend_lock(sessions, backend_pid=pid)
                assert telegram.plain_calls == []
            finally:
                release.set()
                await asyncio.wait_for(receiver, 10)
            with pytest.raises(ReplySendError) as error:
                await asyncio.wait_for(sender, 10)
            assert error.value.code == "context_changed"
        assert telegram.plain_calls == []
