"""Actual durable-ingress/send races in isolated PostgreSQL; no Telegram I/O."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api.mcp_routes import _send_approved_reply_tool_result
from app.application.reply_drafts import ReplyDraftError, SaveReplyDraftService
from app.application.reply_sends import ReplySendError
from app.application.telegram_processing import AcceptTelegramReceiptCommand
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.dialog_snapshots import dialog_snapshot
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import Conversation, ReplyDialogVersion, ReplyDraft, SendAttempt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_reply_sends_postgres import (
    _command,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
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
