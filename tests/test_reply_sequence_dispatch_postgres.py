"""Real PostgreSQL acceptance barriers for the shared #718/#716 authority.

No live Telegram transport or production database is used. CI supplies PG17;
without DATABASE_URL these cases are explicitly skipped, never reported passed.
"""

import asyncio
import importlib
import pkgutil
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import app.models
import pytest
from app.application.reply_sequences import QueueApprovedReplySequenceCommand, ReplySequenceError
from app.application.telegram_processing import AcceptTelegramReceiptCommand, TelegramReceiptError
from app.core.config import Settings
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequence_guard import SequenceGuardUnavailable
from app.infrastructure.reply_sequence_migration import import_legacy, initialize_control
from app.infrastructure.reply_sequence_runtime import RUNTIME_KEY, ReplySequenceRuntime
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import Account, ReplyDraft, SendAttempt, TelegramReceipt
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDialogGuard,
    ReplyDispatchControl,
    ReplyDispatchPermit,
    ReplyInboundSignal,
    ReplySequence,
    ReplySequenceItem,
    ReplySequenceNotification,
    ReplyTransportBudget,
)
from app.services.telegram import TelegramApiError
from app.worker.reply_sequence_dispatch import dispatch_claim
from app.worker.reply_sequence_notifications import notify_once
from sqlalchemy import select, update

from test_reply_sends_postgres import _command, _live_connection, _postgres_sessions, _seed_reply


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def runtime_fixture():
    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module("app.models." + module.name)
    async with _postgres_sessions() as sessions:
        engine = sessions.kw["bind"]
        settings = Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN="123:synthetic",
            GATE_REPLY_SEQUENCE_STOP_ON_INBOUND_ENABLED=True,
            GATE_REPLY_SEQUENCE_DISPATCH_V2_ENABLED=True,
            GATE_REPLY_SEQUENCE_PARALLEL_ENABLED=True,
            GATE_REPLY_SEQUENCE_NOTIFICATIONS_ENABLED=True,
            GATE_REPLY_SEQUENCE_DB_POOL_MODE="direct",
        )
        runtime = ReplySequenceRuntime(
            engine=engine,
            sessions=sessions,
            settings=settings,
            sender_guards=engine,
            ingress_guards=engine,
        )
        sessions.configure(info={RUNTIME_KEY: runtime})
        await initialize_control(runtime)
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            control.mode, control.migration_complete = "parallel", True
            session.add(
                ReplyConnectorAdmission(
                    workspace_ref=runtime.workspace,
                    connector_key="telegram_business",
                    state="healthy",
                    continuity_until=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await session.commit()
        yield runtime, sessions


async def queue(runtime, sessions, seed, revision=0):
    async with sessions() as session:
        return await SqlReplySequenceWriter(session).queue(
            QueueApprovedReplySequenceCommand(
                principal=_command(seed, seed.draft_ids[0]).principal,
                draft_ids=seed.draft_ids,
                confirmed=True,
                expected_account="RU",
                expected_recipient_chat_id=seed.recipient_chat_id,
                expected_inbound_revision=revision,
            )
        )


def inbound(seed, *, update_id=500, message_id=700, content=None, sender=None):
    payload = {
        "update_id": update_id,
        "business_message": {
            "message_id": message_id,
            "business_connection_id": seed.business_connection_id,
            "chat": {"id": int(seed.recipient_chat_id), "type": "private"},
            "from": {
                "id": int(seed.recipient_chat_id) if sender is None else sender,
                "is_bot": False,
            },
            "date": 100,
            **({"text": "synthetic"} if content is None else content),
        },
    }
    return AcceptTelegramReceiptCommand(
        bot_key="telegram_business",
        update_id=update_id,
        payload=payload,
        received_at=datetime.now(UTC),
    )


async def accept(sessions, command):
    async with sessions() as session:
        return await SqlTelegramReceiptStore(session).create_if_absent(command)


class FakeTelegram:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error
        self.started = asyncio.Event()
        self.release = None

    async def get_business_connection(self, *, business_connection_id):
        return _live_connection(business_connection_id)

    async def send_plain_message(self, **payload):
        self.calls.append(payload)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise self.error
        return {"ok": True, "result": {"message_id": 900 + len(self.calls)}}


async def clear_budget(sessions):
    async with sessions() as session:
        await session.execute(
            update(ReplyTransportBudget).values(
                next_allowed_at=datetime.now(UTC) - timedelta(seconds=2)
            )
        )
        await session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        {"text": "угу"},
        {"voice": {"file_id": "synthetic"}},
        {"document": {"file_id": "synthetic"}},
        {"photo": []},
        {"unknown_media_subtype": {}},
        {},
    ],
)
async def test_any_new_inbound_stops_atomically_before_content_processing(content):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        await accept(sessions, inbound(seed, content=content))
        async with sessions() as session:
            sequence = await session.get(ReplySequence, queued.sequence_id)
            assert sequence.state == "stopped" and sequence.stop_reason == "recipient_replied"
            assert (await session.scalars(select(ReplySequenceItem.state))).all() == [
                "cancelled"
            ] * 2
            assert len((await session.scalars(select(ReplySequenceNotification))).all()) == 1
            assert len((await session.scalars(select(ReplyInboundSignal))).all()) == 1
        assert await runtime.claim(queued.sequence_id) is None


@pytest.mark.anyio
async def test_provider_message_duplicate_does_not_advance_revision_and_mutation_rolls_back():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(runtime, sessions, seed)
        command = inbound(seed)
        receipt = await accept(sessions, command)
        replay = await accept(sessions, command)
        assert replay.duplicate and replay.receipt_id == receipt.receipt_id
        await accept(sessions, inbound(seed, update_id=501))
        with pytest.raises(TelegramReceiptError):
            await accept(sessions, inbound(seed, update_id=502, content={"text": "changed"}))
        async with sessions() as session:
            guard = (await session.scalars(select(ReplyDialogGuard))).one()
            assert guard.inbound_revision == 1
            assert len((await session.scalars(select(TelegramReceipt))).all()) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["owner", "edited", "service", "group", "foreign_connector"])
async def test_non_inbound_events_do_not_stop(case):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        command = inbound(seed, sender=999 if case == "owner" else None)
        payload = command.payload
        if case == "edited":
            payload["edited_business_message"] = payload.pop("business_message")
        elif case == "service":
            payload["business_message"]["pinned_message"] = {}
        elif case == "group":
            payload["business_message"]["chat"]["type"] = "group"
        elif case == "foreign_connector":
            command = replace(command, bot_key="telegram_alpha_group")
        await accept(sessions, command)
        async with sessions() as session:
            assert (await session.get(ReplySequence, queued.sequence_id)).state == "active"


@pytest.mark.anyio
async def test_concurrent_claims_have_one_winner_and_no_second_send():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        claims = await asyncio.gather(*(runtime.claim(queued.sequence_id) for _ in range(8)))
        claim = next(c for c in claims if c)
        assert sum(c is not None for c in claims) == 1
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        await dispatch_claim(runtime, telegram, claim)
        assert len(telegram.calls) == 1
        assert await runtime.claim(queued.sequence_id) is None


@pytest.mark.anyio
async def test_slow_series_and_another_account_overlap_with_exact_routing():
    async with runtime_fixture() as (runtime, sessions):
        a, b = (
            await _seed_reply(sessions, draft_count=2),
            await _seed_reply(sessions, draft_count=2),
        )
        qa, qb = await queue(runtime, sessions, a), await queue(runtime, sessions, b)
        ca = await runtime.claim(qa.sequence_id)
        slow, fast = FakeTelegram(), FakeTelegram()
        slow.release = asyncio.Event()
        task = asyncio.create_task(dispatch_claim(runtime, slow, ca))
        await asyncio.wait_for(slow.started.wait(), 3)
        await clear_budget(sessions)
        cb = await runtime.claim(qb.sequence_id)
        assert cb is not None
        await asyncio.wait_for(dispatch_claim(runtime, fast, cb), 3)
        assert not task.done() and len(fast.calls) == 1
        assert slow.calls[0]["business_connection_id"] == a.business_connection_id
        assert fast.calls[0]["business_connection_id"] == b.business_connection_id
        slow.release.set()
        await asyncio.wait_for(task, 3)


@pytest.mark.anyio
async def test_ingress_cannot_ack_while_sender_owns_transport_barrier():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        claim = await runtime.claim(queued.sequence_id)
        telegram = FakeTelegram()
        telegram.release = asyncio.Event()
        task = asyncio.create_task(dispatch_claim(runtime, telegram, claim))
        await asyncio.wait_for(telegram.started.wait(), 3)
        with pytest.raises(SequenceGuardUnavailable):
            await asyncio.wait_for(accept(sessions, inbound(seed)), 3)
        async with sessions() as session:
            assert not (await session.scalars(select(TelegramReceipt))).all()
        telegram.release.set()
        await asyncio.wait_for(task, 3)
        await accept(sessions, inbound(seed))
        async with sessions() as session:
            assert (await session.get(ReplySequence, queued.sequence_id)).state == "stopped"
            states = (
                await session.scalars(
                    select(ReplySequenceItem.state).order_by(ReplySequenceItem.position)
                )
            ).all()
            assert states == ["sent", "cancelled"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,expected",
    [
        (TimeoutError(), "unknown"),
        (TelegramApiError(error_code=500, http_status=500, description="synthetic"), "unknown"),
        (
            TelegramApiError(
                error_code=429, http_status=429, description="synthetic", retry_after_seconds=120
            ),
            "failed",
        ),
    ],
)
async def test_failure_stops_tail_without_retry_and_preserves_outcome(error, expected):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        claim = await runtime.claim(queued.sequence_id)
        telegram = FakeTelegram(error=error)
        await dispatch_claim(runtime, telegram, claim)
        await runtime.reconcile()
        assert await runtime.claim(queued.sequence_id) is None
        assert len(telegram.calls) == 1
        async with sessions() as session:
            states = (
                await session.scalars(
                    select(ReplySequenceItem.state).order_by(ReplySequenceItem.position)
                )
            ).all()
            assert states == [expected, "cancelled"]
            if isinstance(error, TelegramApiError) and error.error_code == 429:
                assert all(
                    b.blocked_until is not None
                    for b in (await session.scalars(select(ReplyTransportBudget))).all()
                )


@pytest.mark.anyio
async def test_stale_revision_and_direct_cancelled_send_are_denied():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        await accept(sessions, inbound(seed))
        with pytest.raises(ReplySequenceError, match="recipient context changed"):
            await queue(runtime, sessions, seed, revision=0)
        with pytest.raises(ReplySequenceError, match="recipient context pending"):
            await queue(runtime, sessions, seed, revision=1)
        await accept(sessions, inbound(seed, update_id=501, message_id=701))
        telegram = FakeTelegram()
        from app.application.reply_sends import ReplySendError

        async with sessions() as session:
            with pytest.raises(ReplySendError) as exc:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=runtime.settings
                ).send(_command(seed, seed.draft_ids[0]))
            assert exc.value.code == "sequence_stopped"
        assert not telegram.calls
        async with sessions() as session:
            sequence = await session.get(ReplySequence, queued.sequence_id)
            assert sequence.state == "stopped"


@pytest.mark.anyio
async def test_approved_text_mutation_stops_before_external_effect():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        claim = await runtime.claim(queued.sequence_id)
        async with sessions() as session:
            await session.execute(
                update(ReplyDraft)
                .where(ReplyDraft.id == claim.draft_id)
                .values(draft_text="changed")
            )
            await session.commit()
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert not telegram.calls
        async with sessions() as session:
            sequence = await session.get(ReplySequence, queued.sequence_id)
            assert sequence.state == "stopped" and sequence.stop_reason == "approval_changed"
            assert not (await session.scalars(select(SendAttempt))).all()


@pytest.mark.anyio
async def test_global_permits_and_same_account_exclusion_are_durable():
    async with runtime_fixture() as (runtime, sessions):
        seeds = [await _seed_reply(sessions, draft_count=2) for _ in range(6)]
        queued = [await queue(runtime, sessions, s) for s in seeds]
        claims = await asyncio.gather(*(runtime.claim(q.sequence_id) for q in queued))
        assert sum(c is not None for c in claims) == 4
        async with sessions() as session:
            slots = (
                await session.scalars(
                    select(ReplyDispatchPermit).where(ReplyDispatchPermit.state == "preflight")
                )
            ).all()
            assert len(slots) == 4
        with pytest.raises(ReplySequenceError, match="sequence already active"):
            await queue(runtime, sessions, seeds[0])


@pytest.mark.anyio
async def test_notification_route_is_checked_and_unknown_is_never_retried():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(runtime, sessions, seed)
        await accept(sessions, inbound(seed))
        telegram = FakeTelegram(error=TimeoutError())
        await notify_once(runtime, telegram)
        assert not telegram.calls
        async with sessions() as session:
            event = (await session.scalars(select(ReplySequenceNotification))).one()
            assert event.delivery_state == "blocked_route"
            event.delivery_state = "pending"
            account = await session.get(Account, seed.account_id)
            account.permissions = {"reply_sequence_owner_subject": "reply-send-postgres-test"}
            await session.commit()
        await notify_once(runtime, telegram)
        await notify_once(runtime, telegram)
        assert len(telegram.calls) == 1 and telegram.calls[0]["chat_id"] == "999"
        async with sessions() as session:
            assert (
                await session.scalars(select(ReplySequenceNotification.delivery_state))
            ).one() == "unknown"


@pytest.mark.anyio
async def test_legacy_import_is_terminal_and_repeatable_without_notification_storm():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=50)
        async with sessions() as session:
            session.info.pop(RUNTIME_KEY)
            queued = await SqlReplySequenceWriter(session).queue(
                QueueApprovedReplySequenceCommand(
                    principal=_command(seed, seed.draft_ids[0]).principal,
                    draft_ids=seed.draft_ids,
                    confirmed=True,
                    expected_account="RU",
                    expected_recipient_chat_id=seed.recipient_chat_id,
                )
            )
            await session.commit()
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            control.mode = "disabled"
            await session.commit()
        first = await import_legacy(runtime, legacy_workers_drained=True)
        second = await import_legacy(runtime, legacy_workers_drained=True)
        assert first["imported"] == 1 and second["existing"] == 1
        async with sessions() as session:
            sequence = await session.get(ReplySequence, queued.sequence_id)
            assert (
                sequence.state == "stopped" and sequence.stop_reason == "legacy_reapproval_required"
            )
            assert len((await session.scalars(select(ReplySequenceItem))).all()) == 50
            assert not (await session.scalars(select(ReplySequenceNotification))).all()
