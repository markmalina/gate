"""Real PostgreSQL checks for terminal stop after ingress continuity loss."""

from datetime import UTC, datetime, timedelta

import pytest
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDispatchControl,
    ReplySequence,
    ReplySequenceItem,
    ReplySequenceNotification,
)
from app.worker.reply_sequence_continuity import reconcile_continuity_once
from sqlalchemy import select
from test_reply_sends_postgres import _seed_reply
from test_reply_sequence_dispatch_postgres import queue, runtime_fixture


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_healthy_evidence_does_not_change_generation_or_approval():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        async with sessions() as session:
            before = await session.get(ReplyDispatchControl, runtime.workspace)
            generation = before.generation
        assert await reconcile_continuity_once(runtime) == 0
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            sequence = await session.get(ReplySequence, queued.sequence_id)
            assert control.mode == "parallel" and control.generation == generation
            assert sequence.state == "active"


@pytest.mark.anyio
@pytest.mark.parametrize("loss", ["expiry", "restart"])
async def test_expiry_or_restart_fences_before_terminal_stop_without_auto_resume(loss):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        if loss == "expiry":
            async with sessions() as session:
                connector = (
                    await session.scalars(select(ReplyConnectorAdmission))
                ).one()
                connector.continuity_until = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
        else:
            await runtime.invalidate_ingress("ingress_runtime_started")
        assert await reconcile_continuity_once(runtime) == 1
        assert await reconcile_continuity_once(runtime) == 0
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            sequence = await session.get(ReplySequence, queued.sequence_id)
            items = (
                await session.scalars(
                    select(ReplySequenceItem).where(
                        ReplySequenceItem.sequence_id == queued.sequence_id
                    )
                )
            ).all()
            notices = (await session.scalars(select(ReplySequenceNotification))).all()
            assert control.mode == "disabled"
            assert sequence.state == "stopped"
            assert sequence.stop_reason == "ingress_continuity_lost"
            assert all(item.state == "cancelled" for item in items)
            assert len(notices) == 1
        assert await runtime.claim(queued.sequence_id) is None
