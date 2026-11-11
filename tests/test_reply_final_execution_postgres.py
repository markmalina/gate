"""Gate844 final execution authority, real SQL waits and synthetic transports."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.reply_drafts import SaveReplyDraftService
from app.application.reply_sends import ReplySendError
from app.infrastructure import reply_sends
from app.infrastructure.dialog_snapshots import INGRESS_CHAT, INGRESS_FENCE, lock_dialog_keys
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.models.domain import SendAttempt
from app.models.reply_media import ReplyMediaBudgetReservation, ReplyMediaExecutionBudget
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from test_reply_formatting_send import RecordingTelegram
from test_reply_media_transport import MediaTelegram, prepared
from test_reply_rich_send import RichTelegram, save_rich
from test_reply_sends_postgres import (
    _attach_document,
    _command,
    _document_command,
    _DocumentTelegram,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
    _settings,
    _wait_for_backend_lock,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _budgeted_case(sessions, mode):
    seed = await _seed_reply(sessions)
    command = _command(seed, seed.draft_ids[0])
    settings = _settings().model_copy(
        update={
            "gate_document_send_enabled": True,
            "gate_rich_send_enabled": True,
            "gate_rich_media_send_enabled": True,
        }
    )
    if mode in {"photo", "album", "rich_photo", "rich_slideshow", "rich_collage"}:
        _, command = await prepared(sessions, seed, mode)
        telegram = MediaTelegram()
    else:
        if mode == "rich":
            saved = await save_rich(sessions, seed)
            command = replace(
                _command(seed, saved.draft_id),
                expected_payload_sha256=saved.formatting.payload_sha256,
            )
            telegram = RichTelegram()
        elif mode == "markdown":
            async with sessions() as s:
                saved = await SaveReplyDraftService(SqlReplyDraftWriter(s)).execute(
                    replace(
                        _save_draft_command(seed, "Reviewed **text**"),
                        text_format="markdown",
                    )
                )
                await s.commit()
            command = replace(
                _command(seed, saved.draft_id),
                expected_payload_sha256=saved.formatting.payload_sha256,
            )
            telegram = RecordingTelegram()
        elif mode == "document":
            data = await _attach_document(sessions, seed)
            command, telegram = _document_command(seed, data), _DocumentTelegram()
        else:
            telegram = _LiteralRecordingTelegram()
        command = replace(command, execution_id="review-bounded-case")
        async with sessions.begin() as s:
            s.add(
                ReplyMediaExecutionBudget(
                    workspace_id=command.principal.access_scope.workspace_id,
                    principal_subject=command.principal.subject,
                    execution_id=command.execution_id,
                    expires_at=datetime.now(UTC) + timedelta(minutes=2),
                )
            )
    return seed, command, telegram, settings


def _writes(telegram):
    return getattr(telegram, "calls", getattr(telegram, "plain_calls", []))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode",
    [
        "plain",
        "markdown",
        "rich",
        "document",
        "photo",
        "album",
        "rich_photo",
        "rich_slideshow",
        "rich_collage",
    ],
)
@pytest.mark.parametrize("expired", [False, True])
async def test_execution_deadline_after_waiting_for_final_fence(mode, expired, monkeypatch):
    async with _postgres_sessions() as sessions:
        _seed, command, telegram, settings = await _budgeted_case(sessions, mode)
        async with sessions() as s:
            budget = await s.scalar(select(ReplyMediaExecutionBudget))
            deadline = budget.expires_at
        reserved, proceed = asyncio.Event(), asyncio.Event()

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return deadline + timedelta(seconds=1)

        class Sender(SqlTelegramReplySender):
            async def _reserve(self, **kwargs):
                result = await super()._reserve(**kwargs)
                reserved.set()
                await asyncio.wait_for(proceed.wait(), 10)
                return result

        async with sessions() as guard:
            async with sessions.kw["bind"].connect() as pinned:
                pid = await pinned.scalar(text("select pg_backend_pid()"))
                await pinned.rollback()

                async def send():
                    async with AsyncSession(bind=pinned, expire_on_commit=False) as s:
                        return await Sender(session=s, telegram=telegram, settings=settings).send(
                            command,
                        )

                sending = asyncio.create_task(send())
                try:
                    await asyncio.wait_for(reserved.wait(), 10)
                    await lock_dialog_keys(guard, keys=[(INGRESS_FENCE, INGRESS_CHAT)])
                    proceed.set()
                    await _wait_for_backend_lock(sessions, backend_pid=pid)
                    if expired:
                        monkeypatch.setattr(reply_sends, "datetime", ExpiredClock)
                    await guard.commit()
                    if expired:
                        with pytest.raises(ReplySendError) as denied:
                            await asyncio.wait_for(sending, 10)
                        assert denied.value.code == "media_execution_expired"
                    else:
                        assert (await asyncio.wait_for(sending, 10)).sent
                finally:
                    proceed.set()
                    await guard.rollback()
                    if not sending.done():
                        sending.cancel()
                        await asyncio.gather(sending, return_exceptions=True)
        async with sessions() as s:
            attempt = await s.scalar(select(SendAttempt))
            budget = await s.scalar(select(ReplyMediaExecutionBudget))
            expected_messages = 3 if mode == "album" else 1
            assert budget.expires_at == deadline
            assert budget.writes_reserved == 1 and budget.messages_reserved == expected_messages
            assert (
                await s.scalar(select(func.count()).select_from(ReplyMediaBudgetReservation)) == 1
            )
            outcome = "failed" if expired else "sent"
            assert attempt.status == outcome
            await s.rollback()
            # Terminal replay must neither renew TTL nor spend a second slot.
            monkeypatch.setattr(reply_sends, "datetime", ExpiredClock)
            replay = await SqlTelegramReplySender(
                session=s,
                telegram=telegram,
                settings=settings,
            ).send(command)
            assert replay.idempotent_replay and replay.status == outcome
        assert len(_writes(telegram)) == (0 if expired else 1)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        "missing_reservation",
        "workspace",
        "principal",
        "execution",
        "wrong_reservation",
        "shortened_deadline",
    ],
)
async def test_final_admission_requires_the_same_scoped_durable_budget(change):
    async with _postgres_sessions() as sessions:
        _seed, command, telegram, settings = await _budgeted_case(sessions, "plain")

        class Sender(SqlTelegramReplySender):
            async def _reserve(self, **kwargs):
                result = await super()._reserve(**kwargs)
                async with sessions.begin() as s:
                    budget = await s.scalar(select(ReplyMediaExecutionBudget))
                    if change == "missing_reservation":
                        await s.execute(delete(ReplyMediaBudgetReservation))
                    elif change == "shortened_deadline":
                        budget.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    elif change == "wrong_reservation":
                        other = ReplyMediaExecutionBudget(
                            workspace_id="foreign",
                            principal_subject="foreign",
                            execution_id="foreign",
                            expires_at=budget.expires_at,
                            writes_reserved=1,
                            messages_reserved=1,
                        )
                        s.add(other)
                        await s.flush()
                        await s.execute(
                            update(ReplyMediaBudgetReservation).values(budget_id=other.id)
                        )
                    else:
                        setattr(
                            budget,
                            {
                                "workspace": "workspace_id",
                                "principal": "principal_subject",
                                "execution": "execution_id",
                            }[change],
                            "foreign",
                        )
                return result

        async with sessions() as s:
            with pytest.raises(ReplySendError) as denied:
                await Sender(session=s, telegram=telegram, settings=settings).send(command)
            assert denied.value.code == "media_execution_expired"
            budgets = (await s.scalars(select(ReplyMediaExecutionBudget))).all()
            assert all(b.writes_reserved == b.messages_reserved == 1 for b in budgets)
            assert (await s.scalar(select(SendAttempt))).status == "failed"
        assert not _writes(telegram)


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["missing", "workspace", "principal", "execution"])
async def test_missing_or_foreign_execution_budget_refuses_before_reservation(change):
    async with _postgres_sessions() as sessions:
        _seed, command, telegram, settings = await _budgeted_case(sessions, "plain")
        async with sessions.begin() as s:
            budget = await s.scalar(select(ReplyMediaExecutionBudget))
            if change == "missing":
                await s.delete(budget)
            else:
                setattr(
                    budget,
                    {
                        "workspace": "workspace_id",
                        "principal": "principal_subject",
                        "execution": "execution_id",
                    }[change],
                    "foreign",
                )
        async with sessions() as s:
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(
                    session=s,
                    telegram=telegram,
                    settings=settings,
                ).send(command)
            assert denied.value.code == "media_execution_expired"
            await s.rollback()
            assert await s.scalar(select(func.count()).select_from(SendAttempt)) == 0
            assert (
                await s.scalar(select(func.count()).select_from(ReplyMediaBudgetReservation)) == 0
            )
        assert not _writes(telegram)
