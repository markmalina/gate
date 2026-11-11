"""Gate844 regressions adapted from Delta520 probes; synthetic PostgreSQL only."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from app.application.mcp_inbox import get_reply_context
from app.application.reply_drafts import SaveReplyDraftService
from app.application.reply_sends import ReplySendError
from app.infrastructure import reply_sends
from app.infrastructure.dialog_snapshots import INGRESS_CHAT, INGRESS_FENCE, lock_dialog_keys
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.models.domain import Account, SendAttempt
from app.models.reply_media import ReplyMediaExecutionBudget
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from test_dialog_snapshots_postgres import receive
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
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("flag", ["send_enabled", "is_active"])
async def test_committed_account_disable_while_final_admission_waits_must_prevent_write(flag):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        reserved, proceed = asyncio.Event(), asyncio.Event()
        telegram = _LiteralRecordingTelegram()
        async with sessions() as guard:
            async with sessions.kw["bind"].connect() as pinned:
                pid = await pinned.scalar(text("select pg_backend_pid()"))
                await pinned.rollback()

                class Sender(SqlTelegramReplySender):
                    async def _reserve(self, **kwargs):
                        result = await super()._reserve(**kwargs)
                        reserved.set()
                        await asyncio.wait_for(proceed.wait(), 10)
                        return result

                async def send():
                    async with AsyncSession(bind=pinned, expire_on_commit=False) as s:
                        return await Sender(
                            session=s, telegram=telegram, settings=_settings()
                        ).send(_command(seed, seed.draft_ids[0]))

                sending = asyncio.create_task(send())
                try:
                    await asyncio.wait_for(reserved.wait(), 10)
                    await lock_dialog_keys(guard, keys=[(INGRESS_FENCE, INGRESS_CHAT)])
                    proceed.set()
                    await _wait_for_backend_lock(sessions, backend_pid=pid)
                    async with sessions.begin() as disable:
                        await disable.execute(
                            update(Account)
                            .where(Account.id == seed.account_id)
                            .values(**{flag: False})
                        )
                    # This COMMIT precedes the final admission's fence acquisition.
                    await guard.commit()
                    try:
                        result = await asyncio.wait_for(sending, 10)
                    except ReplySendError as e:
                        assert e.code == "account_send_disabled"
                    else:
                        assert not result.sent, "committed account disable was ignored"
                    assert telegram.plain_calls == []
                finally:
                    proceed.set()
                    await guard.rollback()
                    if not sending.done():
                        sending.cancel()
                        await asyncio.gather(sending, return_exceptions=True)


@pytest.mark.anyio
async def test_account_disable_committed_before_final_read_is_rejected_control():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        telegram = _LiteralRecordingTelegram()

        class Sender(SqlTelegramReplySender):
            async def _reserve(self, **kwargs):
                result = await super()._reserve(**kwargs)
                async with sessions.begin() as disable:
                    await disable.execute(
                        update(Account)
                        .where(Account.id == seed.account_id)
                        .values(send_enabled=False)
                    )
                return result

        async with sessions() as s:
            with pytest.raises(ReplySendError) as denied:
                await Sender(session=s, telegram=telegram, settings=_settings()).send(
                    _command(seed, seed.draft_ids[0])
                )
            assert denied.value.code == "account_send_disabled"
        assert not telegram.plain_calls


@pytest.mark.anyio
@pytest.mark.parametrize("owner", [False, True])
async def test_new_read_and_new_exact_draft_after_voice_refusal(owner):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        command = _command(seed, seed.draft_ids[0])
        telegram = _LiteralRecordingTelegram()
        await receive(
            sessions,
            seed,
            update_id=973,
            owner=owner,
            content={"voice": {"file_id": "synthetic-review-voice"}},
        )
        async with sessions() as s:
            with pytest.raises(ReplySendError) as error:
                await SqlTelegramReplySender(
                    session=s, telegram=telegram, settings=_settings()
                ).send(command)
            assert error.value.code == "context_changed"
            await s.rollback()
            fresh = await get_reply_context(
                session=s, scope=command.principal.access_scope, task_id=seed.task_id, limit=30
            )
            assert fresh["context_ready"] and fresh["dialog_events"][-1]["content_type"] == "voice"
            token = fresh["context_snapshot"]
            await s.rollback()
            draft = await SaveReplyDraftService(SqlReplyDraftWriter(s)).execute(
                replace(
                    _save_draft_command(seed, "Fresh after voice"), expected_context_snapshot=token
                )
            )
            await s.commit()
            result = await SqlTelegramReplySender(
                session=s, telegram=telegram, settings=_settings()
            ).send(replace(command, draft_id=draft.draft_id))
            assert result.sent
            replay = await SqlTelegramReplySender(
                session=s, telegram=telegram, settings=_settings()
            ).send(replace(command, draft_id=draft.draft_id))
            assert replay.sent and replay.idempotent_replay
        assert len(telegram.plain_calls) == 1


@pytest.mark.anyio
async def test_no_duplicate_send_after_success_then_new_event_old_receipt_replay():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        command = _command(seed, seed.draft_ids[0])
        telegram = _LiteralRecordingTelegram()
        async with sessions() as s:
            result = await SqlTelegramReplySender(
                session=s, telegram=telegram, settings=_settings()
            ).send(command)
        await receive(sessions, seed, update_id=974, content={"text": "next synthetic event"})
        async with sessions() as s:
            again = await SqlTelegramReplySender(
                session=s, telegram=telegram, settings=_settings()
            ).send(command)
            assert again.sent and again.idempotent_replay and again.attempt_id == result.attempt_id
            attempts = (await s.scalars(select(SendAttempt))).all()
            assert len(attempts) == 1 and attempts[0].status == "sent"
        assert len(telegram.plain_calls) == 1


@pytest.mark.anyio
async def test_plain_shared_execution_expiry_after_reservation_must_prevent_write(monkeypatch):
    from datetime import timedelta

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        command = _command(seed, seed.draft_ids[0])
        deadline = datetime.now(UTC) + timedelta(minutes=2)
        async with sessions.begin() as s:
            s.add(
                ReplyMediaExecutionBudget(
                    workspace_id=command.principal.access_scope.workspace_id,
                    principal_subject=command.principal.subject,
                    execution_id="review-expiring-execution",
                    expires_at=deadline,
                    write_cap=12,
                    message_cap=20,
                    writes_reserved=0,
                    messages_reserved=0,
                )
            )

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return deadline + timedelta(seconds=1)

        class Sender(SqlTelegramReplySender):
            async def _reserve(self, **kwargs):
                result = await super()._reserve(**kwargs)
                # Reservation was valid. The final-admission clock is now past TTL.
                monkeypatch.setattr(reply_sends, "datetime", ExpiredClock)
                return result

        telegram = _LiteralRecordingTelegram()
        async with sessions() as s:
            try:
                result = await Sender(session=s, telegram=telegram, settings=_settings()).send(
                    replace(command, execution_id="review-expiring-execution")
                )
            except ReplySendError as denied:
                assert denied.code == "media_execution_expired"
            else:
                assert not result.sent, "expired shared execution still dispatched text"
        assert not telegram.plain_calls
