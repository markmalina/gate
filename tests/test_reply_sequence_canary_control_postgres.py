"""Real synthetic DB checks for canary arming and terminal scope fencing."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.reply_sequence_policy import text_digest
from app.infrastructure.reply_sequence_canary import (
    CanaryAttemptRequest,
    CanaryDenied,
    reserve_canary_attempt,
)
from app.infrastructure.reply_sequence_operations import (
    CanaryArm,
    CanaryGrant,
    arm_canary,
    canary_status,
    close_canary,
    reconcile_canary_once,
)
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime
from app.models.domain import Account, ReplyDraft
from app.models.reply_sequences import (
    ReplyCanaryScope,
    ReplyDispatchControl,
    ReplySequence,
    ReplySequenceNotification,
)
from app.worker.reply_sequence_notifications import notify_once
from sqlalchemy import select

from test_reply_sends_postgres import _seed_reply
from test_reply_sequence_dispatch_postgres import (
    FakeTelegram,
    accept,
    inbound,
    queue,
    runtime_fixture,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def prepare(runtime, sessions):
    seed = await _seed_reply(sessions, draft_count=2)
    runtime.settings.gate_reply_sequence_canary_enabled = True
    async with sessions() as session:
        account = await session.get(Account, seed.account_id)
        account.permissions = {"reply_sequence_owner_subject": "reply-send-postgres-test"}
        control = await session.get(ReplyDispatchControl, runtime.workspace)
        control.mode = "disabled"
        drafts = (
            await session.scalars(select(ReplyDraft).where(ReplyDraft.id.in_(seed.draft_ids)))
        ).all()
        request = CanaryArm(
            execution_id="synthetic-execution",
            principal_subject="reply-send-postgres-test",
            expected_generation=control.generation,
            bot_resource_id=runtime.bot_resource,
            grants=tuple(
                CanaryGrant(
                    seed.account_id, seed.recipient_chat_id, draft.id, text_digest(draft.draft_text)
                )
                for draft in drafts
            ),
            notification_destination="999",
            expires_at=datetime.now(UTC) + timedelta(minutes=9),
            max_attempts=6,
        )
        await session.commit()
    return seed, request


async def activate(runtime, sessions):
    async with sessions() as session:
        control = await session.get(ReplyDispatchControl, runtime.workspace)
        control.mode = "serial"
        control.generation += 1
        await session.commit()


@pytest.mark.anyio
async def test_rearm_is_reconciliation_without_reset_or_scope_extension():
    async with runtime_fixture() as (runtime, sessions):
        seed, request = await prepare(runtime, sessions)
        first = await arm_canary(runtime, request)
        grant = request.grants[0]
        async with sessions() as session:
            await reserve_canary_attempt(
                session,
                CanaryAttemptRequest(
                    workspace_ref=runtime.workspace,
                    execution_id=request.execution_id,
                    attempt_key=uuid4(),
                    kind="sequence",
                    principal_subject=request.principal_subject,
                    account_id=seed.account_id,
                    dialog_ref=seed.recipient_chat_id,
                    body_sha256=grant.body_sha256,
                    notification_destination=None,
                    notification_template=None,
                ),
            )
        replay = await arm_canary(runtime, request)
        assert replay["scope_sha256"] == first["scope_sha256"]
        assert replay["attempts_reserved"] == 1
        for changed in [
            replace(request, max_attempts=5),
            replace(request, expires_at=request.expires_at + timedelta(seconds=1)),
            replace(request, execution_id="second-execution"),
            replace(request, principal_subject="another-owner"),
        ]:
            with pytest.raises(CanaryDenied, match="immutable"):
                await arm_canary(runtime, changed)
        await close_canary(runtime, execution_id=request.execution_id)
        replay = await arm_canary(runtime, request)
        assert replay["state"] == "closed" and replay["attempts_reserved"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["body", "route", "generation", "active"])
async def test_arm_rejects_changed_draft_route_generation_and_undrained_state(change):
    async with runtime_fixture() as (runtime, sessions):
        seed, request = await prepare(runtime, sessions)
        async with sessions() as session:
            if change == "body":
                draft = await session.get(ReplyDraft, request.grants[0].draft_id)
                draft.draft_text = "changed after approval"
            if change == "route":
                account = await session.get(Account, seed.account_id)
                account.permissions = {}
            if change == "generation":
                request = replace(request, expected_generation=request.expected_generation + 1)
            if change == "active":
                control = await session.get(ReplyDispatchControl, runtime.workspace)
                control.mode = "parallel"
            await session.commit()
        with pytest.raises(CanaryDenied):
            await arm_canary(runtime, request)
        assert (await canary_status(runtime))["state"] == "unarmed"


@pytest.mark.anyio
@pytest.mark.parametrize("loss", ["flag_off", "expiry", "cap"])
async def test_bound_tail_and_notification_cannot_escape_after_scope_loss(loss):
    async with runtime_fixture() as (runtime, sessions):
        seed, request = await prepare(runtime, sessions)
        await arm_canary(runtime, request)
        await activate(runtime, sessions)
        result = await queue(runtime, sessions, seed)
        async with sessions() as session:
            sequence = await session.get(ReplySequence, result.sequence_id)
            assert sequence.canary_execution_id == request.execution_id
            session.add(
                ReplySequenceNotification(
                    sequence_id=sequence.id,
                    kind="stop",
                    delivery_state="pending",
                    next_attempt_at=datetime.now(UTC),
                )
            )
            scope = await session.get(ReplyCanaryScope, runtime.workspace)
            if loss == "expiry":
                scope.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            if loss == "cap":
                scope.attempts_reserved = scope.max_attempts
            await session.commit()
        if loss == "flag_off":
            runtime.settings.gate_reply_sequence_canary_enabled = False
        assert await runtime.claim(result.sequence_id) is None
        telegram = FakeTelegram()
        await notify_once(runtime, telegram)
        assert not telegram.calls
        await reconcile_canary_once(runtime)
        await reconcile_canary_once(runtime)
        async with sessions() as session:
            assert (await session.get(ReplySequence, result.sequence_id)).state == "stopped"
            assert (await session.get(ReplyDispatchControl, runtime.workspace)).mode == "disabled"
            notice = (await session.scalars(select(ReplySequenceNotification))).one()
            assert notice.delivery_state == "failed"
        assert (await canary_status(runtime))["state"] == "closed"


@pytest.mark.anyio
async def test_foreign_notification_backlog_is_refused_before_transport():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(runtime, sessions, seed)
        await accept(sessions, inbound(seed))
        async with sessions() as session:
            account = await session.get(Account, seed.account_id)
            account.permissions = {"reply_sequence_owner_subject": "reply-send-postgres-test"}
            await session.commit()
        runtime.settings.gate_reply_sequence_canary_enabled = True
        telegram = FakeTelegram()
        await notify_once(runtime, telegram)
        assert not telegram.calls
        async with sessions() as session:
            notice = (await session.scalars(select(ReplySequenceNotification))).one()
            assert notice.safe_error_code == "canary_admission_closed"


@pytest.mark.anyio
async def test_control_runtime_does_not_require_or_register_telegram_credential():
    async with runtime_fixture() as (runtime, sessions):
        settings = runtime.settings.model_copy(update={"telegram_bot_token": ""})
        control = ReplySequenceRuntime(
            engine=sessions.kw["bind"],
            settings=settings,
            sessions=sessions,
            sender_guards=sessions.kw["bind"],
            ingress_guards=sessions.kw["bind"],
            bot_resource_id=runtime.bot_resource,
        )
        assert control.bot_resource == runtime.bot_resource
        with pytest.raises(ValueError, match="cannot register"):
            control.register_transport_gate()
