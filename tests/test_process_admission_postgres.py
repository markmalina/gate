"""Real DB transport refusal must retain a proven no-HTTP outcome."""

import httpx
import pytest
from app.infrastructure.database import DatabaseRuntime
from app.infrastructure.process_admission import runtime_process_admission
from app.models.domain import Account, SendAttempt
from app.models.reply_sequences import ReplySequenceNotification
from app.services.process_send_fence import (
    BaselineSendClosed,
    ProcessSendFence,
    install_process_send_fence,
)
from app.services.telegram import TelegramClient
from app.worker.reply_sequence_dispatch import dispatch_claim
from app.worker.reply_sequence_notifications import notify_once
from sqlalchemy import select, text

from test_reply_sends_postgres import _seed_reply
from test_reply_sequence_dispatch_postgres import accept, inbound, queue, runtime_fixture
from test_reply_sequence_epoch_postgres import prepare_epoch


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_admitted_sequence_local_fence_denial_is_cancelled_not_provider_unknown(monkeypatch):
    from app.infrastructure.reply_sends import SqlTelegramReplySender

    monkeypatch.setattr(SqlTelegramReplySender, "_dispatch_clock", staticmethod(lambda: 100.0))
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        claim = await runtime.claim(queued.sequence_id)
        assert claim is not None
        calls = []

        async def deny(resource):
            raise BaselineSendClosed("synthetic_local_health_loss")

        fence = ProcessSendFence(runtime.settings, candidate_mode=True, candidate_health=deny)
        previous = install_process_send_fence(fence)
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: calls.append(request) or httpx.Response(200, json={"ok": True})
                )
            ) as http:
                await dispatch_claim(
                    runtime,
                    TelegramClient(runtime.settings.telegram_bot_token, http_client=http),
                    claim,
                )
            async with sessions() as session:
                attempt = (await session.scalars(select(SendAttempt))).one()
                assert attempt.status == "cancelled"
                assert attempt.error_code == "process_admission_closed"
            assert not calls and fence.inflight == 0
        finally:
            install_process_send_fence(previous)


@pytest.mark.anyio
async def test_pending_notice_local_fence_denial_is_failed_not_unknown():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(runtime, sessions, seed)
        await accept(sessions, inbound(seed))
        async with sessions() as session:
            account = await session.get(Account, seed.account_id)
            account.permissions = {"reply_sequence_owner_subject": "reply-send-postgres-test"}
            await session.commit()
        calls = []
        fence = ProcessSendFence(runtime.settings, closed=True)
        previous = install_process_send_fence(fence)
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: calls.append(request) or httpx.Response(200, json={"ok": True})
                )
            ) as http:
                await notify_once(
                    runtime, TelegramClient(runtime.settings.telegram_bot_token, http_client=http)
                )
            async with sessions() as session:
                notice = (await session.scalars(select(ReplySequenceNotification))).one()
                assert notice.delivery_state == "failed"
                assert notice.safe_error_code == "process_admission_closed"
            assert not calls
        finally:
            install_process_send_fence(previous)


@pytest.mark.anyio
async def test_candidate_startup_bootstrap_epoch_then_mode_opens_only_verified_primary(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        # The shared fixture creates tables directly; install the real ledger
        # row solely for this disposable synthetic DB, before the startup read.
        async with sessions() as session:
            await session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num varchar(32) PRIMARY KEY)"
                )
            )
            await session.execute(text("DELETE FROM alembic_version"))
            await session.execute(
                text("INSERT INTO alembic_version VALUES ('0115_reply_canary_binding')")
            )
            await session.commit()
        database = DatabaseRuntime(sessions.kw["bind"], sessions, runtime)
        calls = []
        async with runtime_process_admission(database, settings=runtime.settings):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: calls.append(request) or httpx.Response(200, json={"ok": True})
                )
            ) as http:
                client = TelegramClient(runtime.settings.telegram_bot_token, http_client=http)
                # No current-source process member exists in the fixture epoch.
                with pytest.raises(BaselineSendClosed):
                    await client.send_plain_message(chat_id="synthetic", text="synthetic")
                assert not calls
                await prepare_epoch(runtime, sessions)
                # prepare_epoch installs observed current source/config/boot and
                # selects parallel in the synthetic fixture. Actual HTTP adapter
                # remains mocked; the G2 admission check is not mocked.
                runtime.register_transport_gate()
                try:
                    await client.send_plain_message(chat_id="synthetic", text="synthetic")
                    assert len(calls) == 1
                    runtime.settings.deployment_sha = "c" * 40
                    with pytest.raises(BaselineSendClosed):
                        await client.send_plain_message(chat_id="synthetic", text="synthetic")
                    assert len(calls) == 1
                finally:
                    await runtime.close()
