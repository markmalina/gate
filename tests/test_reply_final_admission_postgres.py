"""S3 real handlers/SQL fences, independent processes, synthetic recorders only."""

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from app.api import telegram_ingress
from app.application.mcp_inbox import get_reply_context
from app.application.reply_drafts import ReplyDraftError, SaveReplyDraftService
from app.application.reply_sends import ReplySendError
from app.application.telegram_processing import AcceptTelegramReceiptCommand, TelegramReceiptError
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.dialog_snapshots import dialog_snapshot, mark_ingress_unavailable
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import Conversation, ReplyDialogVersion, ReplyTask, SendAttempt
from app.services.telegram import TelegramClient
from app.worker.reply_sequences import run_reply_sequence_once
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from test_dialog_snapshots_postgres import receive
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
    _sequence_command,
    _settings,
    _wait_for_backend_lock,
)
from test_telegram_durable_ingress import _raw_request, _request
from test_telegram_durable_ingress import _settings as ingress_settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def envelope(sessions, payload, update_id):
    async with sessions() as session:
        return await SqlTelegramReceiptStore(session).create_if_absent(
            AcceptTelegramReceiptCommand(
                bot_key=TelegramConnectorKey.BUSINESS.value,
                update_id=update_id,
                payload={"update_id": update_id, **payload},
                received_at=datetime.now(UTC),
            )
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        "connection_disabled",
        "unscoped_event",
        "mutated_replay",
        "unknown_connection",
        "unknown_disabled_connection",
    ],
)
async def test_observed_failures_are_durable_sticky_and_block_prepare_send(failure):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        if failure == "connection_disabled":
            await envelope(
                sessions,
                {
                    "business_connection": {
                        "id": seed.business_connection_id,
                        "is_enabled": False,
                    }
                },
                51,
            )
            # Reconnection cannot certify backfilled history.
            await envelope(
                sessions,
                {
                    "business_connection": {
                        "id": seed.business_connection_id,
                        "is_enabled": True,
                    }
                },
                52,
            )
        elif failure == "unscoped_event":
            await envelope(sessions, {"business_message": {"voice": {}}}, 51)
        elif failure == "mutated_replay":
            await receive(sessions, seed, update_id=51)
            with pytest.raises(TelegramReceiptError):
                await receive(sessions, seed, update_id=51, content={"text": "Changed replay"})
        elif failure == "unknown_disabled_connection":
            await envelope(
                sessions,
                {
                    "business_connection": {
                        "id": "unmapped-disabled-connection",
                        "is_enabled": False,
                    }
                },
                51,
            )
        else:
            await envelope(
                sessions,
                {
                    "business_message": {
                        "business_connection_id": "unmapped-new-connection",
                        "chat": {"id": int(seed.recipient_chat_id)},
                        "voice": {},
                    }
                },
                51,
            )
        await receive(sessions, seed, update_id=53)
        command = _command(seed, seed.draft_ids[0])
        telegram = _LiteralRecordingTelegram()
        async with sessions() as session:
            context = await get_reply_context(
                session=session,
                scope=command.principal.access_scope,
                task_id=seed.task_id,
                limit=30,
            )
            assert not context["context_ready"] and context["context_reason"] == "known_ingress_gap"
            assert context["context_snapshot"] is None
            await session.rollback()
            with pytest.raises(ReplyDraftError) as denied:
                await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                    _save_draft_command(seed, "Cannot prepare"),
                )
            assert denied.value.code == "context_unavailable"
            await session.rollback()
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=_settings(),
                ).send(command)
            assert denied.value.code == "context_unavailable"
        assert telegram.plain_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["message", "edited_message"])
async def test_legacy_business_envelope_is_invalidated_before_async_projection(kind):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        await receive(sessions, seed, kind=kind, content={"voice": {}})
        telegram = _LiteralRecordingTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert denied.value.code == "context_changed"
            await session.rollback()
            context = await get_reply_context(
                session=session,
                scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                task_id=seed.task_id,
                limit=30,
            )
            assert context["dialog_events"][-1]["kind"] == kind
            assert context["dialog_events"][-1]["content_type"] == "voice"
        assert telegram.plain_calls == []


@pytest.mark.anyio
async def test_same_text_new_event_changes_version_but_duplicate_and_other_connection_do_not():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        other_account = await _seed_reply(sessions)
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope

        async def token():
            async with sessions() as session:
                return await dialog_snapshot(
                    session,
                    scope=scope,
                    conversation=await session.get(Conversation, seed.conversation_id),
                )

        first = await token()
        await envelope(
            sessions,
            {
                "business_message": {
                    "business_connection_id": other_account.business_connection_id,
                    "chat": {"id": int(seed.recipient_chat_id)},
                    "text": "same",
                }
            },
            60,
        )
        assert await token() == first
        await envelope(
            sessions,
            {
                "business_connection": {
                    "id": other_account.business_connection_id,
                    "is_enabled": False,
                }
            },
            61,
        )
        assert await token() == first
        async with sessions() as session:
            context = await get_reply_context(
                session=session,
                scope=scope,
                task_id=seed.task_id,
                limit=30,
            )
            assert context["context_ready"] and context["context_snapshot"] == first
        await receive(sessions, seed, update_id=62, content={"text": "same"})
        second = await token()
        assert second != first
        await receive(sessions, seed, update_id=62, content={"text": "same"})
        assert await token() == second
        await receive(sessions, seed, update_id=63, content={"text": "same"})
        assert await token() != second


@pytest.mark.anyio
async def test_other_connector_corrupt_replay_does_not_poison_business_admission():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            store = SqlTelegramReceiptStore(session)
            command = AcceptTelegramReceiptCommand(
                bot_key=TelegramConnectorKey.ALPHA_GROUP.value,
                update_id=1,
                payload={"update_id": 1, "message": {"text": "synthetic"}},
                received_at=datetime.now(UTC),
            )
            await store.create_if_absent(command)
            with pytest.raises(TelegramReceiptError):
                await store.create_if_absent(replace(command, payload={"update_id": 1}))
        async with sessions() as session:
            telegram = _LiteralRecordingTelegram()
            result = await SqlTelegramReplySender(
                session=session,
                telegram=telegram,
                settings=_settings(),
            ).send(_command(seed, seed.draft_ids[0]))
            assert result.sent and len(telegram.plain_calls) == 1


@pytest.mark.anyio
async def test_webhook_durable_acceptance_failure_records_gap_if_database_can_write(monkeypatch):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            store = SqlTelegramReceiptStore(session)
            monkeypatch.setattr(
                store,
                "create_if_absent",
                AsyncMock(
                    side_effect=OperationalError("synthetic insert failure", {}, Exception()),
                ),
            )
            with pytest.raises(Exception) as denied:
                await telegram_ingress._accept_durable_receipt(
                    request=_request({"update_id": 10}),
                    x_telegram_secret="test-secret",
                    settings=ingress_settings(),
                    receipt_store=store,
                )
            assert denied.value.status_code == 503
        async with sessions() as session:
            context = await get_reply_context(
                session=session,
                scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                task_id=seed.task_id,
                limit=30,
            )
            assert context["context_reason"] == "known_ingress_gap"


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [b"{", b"[]", b'{"update_id":false}'])
async def test_authenticated_invalid_envelope_marks_gap_but_invalid_auth_does_not(payload):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            store = SqlTelegramReceiptStore(session)
            with pytest.raises(Exception) as denied:
                await telegram_ingress._accept_durable_receipt(
                    request=_raw_request(payload),
                    x_telegram_secret="wrong",
                    settings=ingress_settings(),
                    receipt_store=store,
                )
            assert denied.value.status_code == 403
            assert list((await session.scalars(select(ReplyDialogVersion))).all()) == []
            await session.rollback()
            with pytest.raises(Exception) as denied:
                await telegram_ingress._accept_durable_receipt(
                    request=_raw_request(payload),
                    x_telegram_secret="test-secret",
                    settings=ingress_settings(),
                    receipt_store=store,
                )
            assert denied.value.status_code == 400
        async with sessions() as session:
            context = await get_reply_context(
                session=session,
                scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                task_id=seed.task_id,
                limit=30,
            )
            assert context["context_reason"] == "known_ingress_gap"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode",
    [
        "plain",
        "markdown",
        "follow_up",
        "document",
        "rich",
        "photo",
        "album",
        "rich_photo",
        "rich_slideshow",
        "rich_collage",
    ],
)
@pytest.mark.parametrize("change", ["voice", "gap"])
async def test_every_mode_rechecks_after_reservation_before_provider(mode, change):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        command = _command(seed, seed.draft_ids[0])
        settings = _settings().model_copy(
            update={
                "gate_document_send_enabled": True,
                "gate_rich_send_enabled": True,
                "gate_rich_media_send_enabled": True,
            }
        )
        if mode == "document":
            data = await _attach_document(sessions, seed)
            command = _document_command(seed, data)
            telegram = _DocumentTelegram()
        elif mode == "rich":
            saved = await save_rich(sessions, seed)
            command = replace(
                _command(seed, saved.draft_id),
                expected_payload_sha256=saved.formatting.payload_sha256,
            )
            telegram = RichTelegram()
        elif mode not in {"plain", "markdown", "follow_up"}:
            _, command = await prepared(sessions, seed, mode)
            telegram = MediaTelegram()
        else:
            telegram = _LiteralRecordingTelegram()
            if mode in {"markdown", "follow_up"}:
                async with sessions() as drafting:
                    if mode == "follow_up":
                        task = await drafting.get(ReplyTask, seed.task_id)
                        task.status = "replied"
                        await drafting.commit()
                    saved = await SaveReplyDraftService(SqlReplyDraftWriter(drafting)).execute(
                        replace(
                            _save_draft_command(seed, "Reviewed reply"),
                            text_format="markdown" if mode == "markdown" else None,
                            follow_up=mode == "follow_up",
                        ),
                    )
                    await drafting.commit()
                    command = _command(seed, saved.draft_id)
                    if saved.formatting is not None:
                        command = replace(
                            command, expected_payload_sha256=saved.formatting.payload_sha256
                        )

        class Sender(SqlTelegramReplySender):
            async def _admit_dispatch(self, **kwargs):
                if change == "voice":
                    await receive(sessions, seed, content={"voice": {"file_id": "synthetic"}})
                else:
                    async with sessions() as ingress:
                        await mark_ingress_unavailable(
                            ingress,
                            reason="synthetic_observed_gap",
                            connection_id=seed.business_connection_id,
                        )
                        await ingress.commit()
                await super()._admit_dispatch(**kwargs)

        async with sessions() as session:
            with pytest.raises(ReplySendError) as denied:
                await Sender(session=session, telegram=telegram, settings=settings).send(command)
            assert denied.value.code == (
                "context_changed" if change == "voice" else "context_unavailable"
            )
            attempt = await session.scalar(select(SendAttempt))
            assert attempt.status == "failed"
        async with sessions() as session:
            replay = await SqlTelegramReplySender(
                session=session,
                telegram=telegram,
                settings=settings,
            ).send(command)
            assert replay.idempotent_replay and replay.status == "failed" and not replay.sent
        assert getattr(telegram, "calls", getattr(telegram, "plain_calls", [])) == []


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["event", "gap"])
async def test_actual_sequence_worker_item_uses_final_admission(monkeypatch, change):
    from app.worker import reply_sequences

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        now = datetime.now(UTC)
        async with sessions() as session:
            await SqlReplySequenceWriter(session, now=now - timedelta(hours=1)).queue(
                _sequence_command(seed, seed.draft_ids),
            )
            await session.commit()

        class Sender(SqlTelegramReplySender):
            async def _admit_dispatch(self, **kwargs):
                if change == "event":
                    await receive(sessions, seed, content={"voice": {}})
                else:
                    async with sessions() as ingress:
                        await mark_ingress_unavailable(
                            ingress,
                            reason="synthetic_observed_gap",
                            connection_id=seed.business_connection_id,
                        )
                        await ingress.commit()
                await super()._admit_dispatch(**kwargs)

        monkeypatch.setattr(reply_sequences, "SqlTelegramReplySender", Sender)
        telegram = _LiteralRecordingTelegram()
        assert (
            await run_reply_sequence_once(
                settings=_settings(),
                session_factory=sessions,
                telegram=telegram,
                now=now,
            )
            == 0
        )
        assert telegram.plain_calls == []
        async with sessions() as session:
            attempt = await session.scalar(select(SendAttempt))
            assert attempt.status == "failed"
            assert attempt.error_code == (
                "context_changed" if change == "event" else "context_unavailable"
            )


@pytest.mark.anyio
async def test_real_postgres_authoritative_version_unavailable_returns_refusal():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            await session.execute(
                text("alter table reply_dialog_versions rename to unavailable_versions")
            )
            await session.commit()
        telegram = _LiteralRecordingTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert denied.value.code == "context_unavailable"
            assert denied.value.message == "authoritative_version_unavailable"
        assert telegram.plain_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["event", "gap"])
async def test_independent_process_commit_precedes_final_admission_and_zero_stale_calls(kind):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        engine = sessions.kw["bind"]
        telegram = _LiteralRecordingTelegram()
        command = _command(seed, seed.draft_ids[0])
        child = None
        final = None
        async with engine.connect() as pinned:
            async with AsyncSession(pinned, expire_on_commit=False) as session:
                sender = SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=_settings()
                )
                intent = await sender._preflight(command)
                probe = await sender._probe_connection(intent)
                reservation = await sender._reserve(command=command, intent=intent, probe=probe)
                schema = await session.scalar(text("select current_schema()"))
                sender_pid = await session.scalar(text("select pg_backend_pid()"))
                await session.rollback()
                try:
                    child = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(Path(__file__).parent / "support/dialog_ingress_process.py"),
                        schema,
                        seed.business_connection_id,
                        seed.recipient_chat_id,
                        kind,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    ready = await asyncio.wait_for(child.stdout.readline(), 15)
                    assert ready.startswith(b"READY "), await child.stderr.read()
                    assert int(ready.split()[1]) != sender_pid
                    final = asyncio.create_task(
                        sender._admit_dispatch(
                            command=command,
                            reservation=reservation,
                        )
                    )
                    await _wait_for_backend_lock(sessions, backend_pid=sender_pid)
                    assert not final.done() and telegram.plain_calls == []
                    child.stdin.write(b"COMMIT\n")
                    await child.stdin.drain()
                    assert await asyncio.wait_for(child.stdout.readline(), 15) == b"COMMITTED\n"
                    assert await asyncio.wait_for(child.wait(), 15) == 0
                    with pytest.raises(ReplySendError) as denied:
                        await asyncio.wait_for(final, 15)
                    assert denied.value.code == (
                        "context_changed" if kind == "event" else "context_unavailable"
                    )
                    assert telegram.plain_calls == []
                finally:
                    if child is not None and child.returncode is None:
                        child.kill()
                        await child.wait()
                    if final is not None and not final.done():
                        final.cancel()
                        await asyncio.gather(final, return_exceptions=True)


@pytest.mark.anyio
async def test_unavailable_authoritative_version_is_explicit_before_any_send(monkeypatch):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        from app.infrastructure import dialog_snapshots

        monkeypatch.setattr(
            dialog_snapshots,
            "dialog_snapshot",
            AsyncMock(
                side_effect=OperationalError("synthetic version read failure", {}, Exception()),
            ),
        )
        telegram = _LiteralRecordingTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=_settings(),
                ).send(_command(seed, seed.draft_ids[0]))
            assert denied.value.code == "context_unavailable"
            assert denied.value.message == "authoritative_version_unavailable"
        assert telegram.plain_calls == []


@pytest.mark.anyio
async def test_legacy_generic_business_transport_cannot_start_http():
    posts = []

    def record(request):
        posts.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        client = TelegramClient("synthetic", http_client=http)
        with pytest.raises(ReplySendError) as denied:
            await client.send_message(chat_id="123", text="old", business_connection_id="synthetic")
        assert denied.value.code == "context_snapshot_required"
        await client.send_message(chat_id="123", text="bot console")
    assert len(posts) == 1
