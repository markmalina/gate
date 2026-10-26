"""Pending tasks are not new dialog events after an explicit snapshot review."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.mcp_inbox import get_reply_context
from app.application.reply_drafts import ReplyDraftError, SaveReplyDraftService
from app.application.reply_sends import ReplySendError
from app.application.reply_sequences import ReplySequenceError
from app.infrastructure.dialog_snapshots import bump_dialog_version
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.models.domain import (
    Message,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
)
from app.worker.reply_sequences import run_reply_sequence_once
from sqlalchemy import func, select
from test_reply_sends_postgres import (
    _command,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
    _sequence_command,
    _settings,
)


class _DistinctTelegram(_LiteralRecordingTelegram):
    async def send_plain_message(self, **kwargs: object) -> dict[str, object]:
        self.plain_calls.append(kwargs)
        return {"ok": True, "result": {"message_id": 1000 + len(self.plain_calls)}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _review_pending_sibling(
    sessions, seed, *, replied: bool = False, newer_than_origin: bool = False,
):
    """Create historical pending work, then actually read its complete context."""
    earlier = datetime.now(UTC) + (
        timedelta(seconds=1) if newer_than_origin else -timedelta(days=1)
    )
    message_id, task_id = uuid4(), uuid4()
    async with sessions() as session:
        session.add(Message(
            id=message_id, bot_id=seed.bot_id, account_id=seed.account_id,
            project_id=seed.project_id, chat_id=seed.recipient_chat_id,
            sender_id=seed.recipient_chat_id, direction="inbound", source="telegram",
            event_type="business_message", text="Initial inbound message", payload={},
            business_connection_id=seed.business_connection_id,
            created_at=earlier, received_at=earlier,
        ))
        await session.flush()
        session.add(ReplyTask(
            id=task_id, account_id=seed.account_id, project_id=seed.project_id,
            conversation_id=seed.conversation_id, trigger_message_id=message_id,
            status=ReplyTaskStatus.NEEDS_REPLY.value, reason="synthetic_old_pending",
            created_at=earlier,
        ))
        if replied:
            current = await session.get(ReplyTask, seed.task_id)
            assert current is not None
            current.status = ReplyTaskStatus.REPLIED.value
        await session.commit()
        context = await get_reply_context(
            session=session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
            task_id=seed.task_id, limit=20,
        )
        assert context is not None and context["context_ready"] is True
        assert len(context["messages"]) == 2
        reviewed = replace(seed, context_snapshot=context["context_snapshot"])
    return reviewed, task_id


async def _save_reviewed(sessions, seed, count: int, *, follow_up: bool = False):
    ids = []
    for index in range(count):
        async with sessions() as session:
            saved = await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                replace(
                    _save_draft_command(seed, f"Reviewed synthetic reply {index}"),
                    follow_up=follow_up,
                ),
            )
            await session.commit()
            ids.append(saved.draft_id)
    return tuple(ids)


@pytest.mark.anyio
@pytest.mark.parametrize("replied", [False, True])
@pytest.mark.parametrize("newer_than_origin", [False, True])
async def test_reviewed_pending_task_allows_single_or_follow_up(
    replied: bool, newer_than_origin: bool,
) -> None:
    async with _postgres_sessions() as sessions:
        seed, old_id = await _review_pending_sibling(
            sessions, await _seed_reply(sessions), replied=replied,
            newer_than_origin=newer_than_origin,
        )
        draft_ids = await _save_reviewed(sessions, seed, 1, follow_up=replied)
        telegram = _DistinctTelegram()
        command = _command(seed, draft_ids[0])
        async with sessions() as session:
            result = await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(command)
        async with sessions() as session:
            replay = await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(command)
        assert result.sent and replay.sent and replay.idempotent_replay
        assert len(telegram.plain_calls) == 1
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 1
            old = await session.get(ReplyTask, old_id)
            assert old is not None and old.status == ReplyTaskStatus.REPLIED.value


@pytest.mark.anyio
@pytest.mark.parametrize("replied", [False, True])
@pytest.mark.parametrize("size", [2, 50])
@pytest.mark.parametrize("newer_than_origin", [False, True])
async def test_reviewed_pending_tasks_allow_ordered_sequence(
    size: int, replied: bool, newer_than_origin: bool,
) -> None:
    async with _postgres_sessions() as sessions:
        seed, _old = await _review_pending_sibling(
            sessions, await _seed_reply(sessions), replied=replied,
            newer_than_origin=newer_than_origin,
        )
        draft_ids = await _save_reviewed(sessions, seed, size, follow_up=replied)
        now = datetime.now(UTC)
        async with sessions() as session:
            queued = await SqlReplySequenceWriter(session, now=now).queue(
                _sequence_command(seed, draft_ids),
            )
            await session.commit()
        assert tuple(item.draft_id for item in queued.items) == draft_ids
        telegram = _DistinctTelegram()
        for position in range(size):
            assert await run_reply_sequence_once(
                settings=_settings(), session_factory=sessions, telegram=telegram,
                now=now + timedelta(minutes=position + 1),
            ) == 1
        assert [call["text"] for call in telegram.plain_calls] == [
            f"Reviewed synthetic reply {position}" for position in range(size)
        ]
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == size


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["draft", "queue", "send"])
@pytest.mark.parametrize("owner", [False, True])
async def test_new_same_text_event_still_blocks_reviewed_pending_work(
    stage: str, owner: bool,
) -> None:
    async with _postgres_sessions() as sessions:
        seed, _old = await _review_pending_sibling(
            sessions, await _seed_reply(sessions), replied=True,
        )
        draft_ids = await _save_reviewed(sessions, seed, 2, follow_up=True)
        telegram = _DistinctTelegram()
        # Same text, distinct event identity, either direction. The durable
        # ingress revision changes before any asynchronous projection is needed.
        async with sessions() as session:
            session.add(Message(
                bot_id=seed.bot_id, account_id=seed.account_id, project_id=seed.project_id,
                chat_id=seed.recipient_chat_id, sender_id="999" if owner else "12345",
                direction="outbound" if owner else "inbound", source="telegram",
                event_type="business_message", text="Initial inbound message", payload={},
                business_connection_id=seed.business_connection_id,
                received_at=datetime.now(UTC),
            ))
            await bump_dialog_version(
                session, connection_id=seed.business_connection_id,
                chat_id=seed.recipient_chat_id,
            )
            await session.commit()
        async with sessions() as session:
            if stage == "draft":
                with pytest.raises(ReplyDraftError) as error:
                    await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                        replace(_save_draft_command(seed, "Stale reply"), follow_up=True),
                    )
            elif stage == "queue":
                with pytest.raises(ReplySequenceError) as error:
                    await SqlReplySequenceWriter(session).queue(
                        _sequence_command(seed, draft_ids),
                    )
            else:
                with pytest.raises(ReplySendError) as error:
                    await SqlTelegramReplySender(
                        session=session, telegram=telegram, settings=_settings(),
                    ).send(_command(seed, draft_ids[0]))
            assert error.value.code == "context_changed"
            await session.rollback()
        assert telegram.plain_calls == []
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
        # Recovery requires a genuine fresh context read and a new exact draft.
        async with sessions() as session:
            context = await get_reply_context(
                session=session, scope=_command(seed, draft_ids[0]).principal.access_scope,
                task_id=seed.task_id, limit=20,
            )
            assert context is not None and context["context_ready"] is True
            fresh = replace(seed, context_snapshot=context["context_snapshot"])
        new_ids = await _save_reviewed(sessions, fresh, 1, follow_up=True)
        async with sessions() as session:
            result = await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(_command(fresh, new_ids[0]))
        assert result.sent and len(telegram.plain_calls) == 1


@pytest.mark.anyio
async def test_pending_sibling_does_not_allow_snapshotless_draft() -> None:
    async with _postgres_sessions() as sessions:
        seed, _old = await _review_pending_sibling(sessions, await _seed_reply(sessions))
        async with sessions() as session:
            with pytest.raises(ReplyDraftError) as error:
                await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                    replace(_save_draft_command(seed, "Missing snapshot"),
                            expected_context_snapshot=None),
                )
            assert error.value.code == "context_snapshot_required"
