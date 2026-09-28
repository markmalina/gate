"""Additional synthetic concurrency/recovery/cutover acceptance for #716/#718."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.api.mcp_schemas import SEQUENCE_DETAIL_SCHEMA, SEQUENCE_SUMMARIES_SCHEMA
from app.application.reply_sequences import ReplySequenceError
from app.core.mcp_security import AccessScope
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequence_guard import dialog_guard
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime
from app.infrastructure.reply_transport_budget import SqlTelegramTransportBudget
from app.models.domain import Conversation, Message, ReplyDraft, ReplyTask, SendAttempt
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyDialogGuard,
    ReplyDispatchControl,
    ReplyDispatchPermit,
    ReplySequence,
    ReplySequenceItem,
    ReplySequenceNotification,
    ReplyTransportBudget,
)
from app.services.telegram import TelegramApiError
from app.worker.reply_sequence_dispatch import dispatch_claim
from jsonschema import Draft202012Validator
from sqlalchemy import delete, select, update

from test_reply_sends_postgres import _command, _seed_reply
from test_reply_sequence_dispatch_postgres import (
    FakeTelegram,
    accept,
    clear_budget,
    inbound,
    queue,
    runtime_fixture,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def same_account(sessions, a, b, recipient="54321"):
    async with sessions() as session:
        for model, identity in (
            (Conversation, b.conversation_id),
            (Message, b.trigger_message_id),
            (ReplyTask, b.task_id),
        ):
            await session.execute(
                update(model)
                .where(model.id == identity)
                .values(account_id=a.account_id, project_id=a.project_id)
            )
        await session.execute(
            update(ReplyDraft)
            .where(ReplyDraft.id.in_(b.draft_ids))
            .values(account_id=a.account_id, project_id=a.project_id)
        )
        for model, identity in ((Conversation, b.conversation_id), (Message, b.trigger_message_id)):
            await session.execute(
                update(model)
                .where(model.id == identity)
                .values(business_connection_id=a.business_connection_id, chat_id=recipient)
            )
        await session.execute(
            update(Message).where(Message.id == b.trigger_message_id).values(sender_id=recipient)
        )
        await session.commit()
    return replace(
        b,
        account_id=a.account_id,
        project_id=a.project_id,
        business_connection_id=a.business_connection_id,
        recipient_chat_id=recipient,
    )


@pytest.mark.anyio
async def test_same_account_distinct_dialogs_overlap_and_account_cap_two(monkeypatch):
    # Test account permits/overlap independently of the runner's commit/fsync speed.
    # Admission expiry is tested with both sides of the deadline below and at HTTP.
    monkeypatch.setattr(SqlTelegramReplySender, "_dispatch_clock", staticmethod(lambda: 100.0))
    async with runtime_fixture() as (runtime, sessions):
        a = await _seed_reply(sessions, draft_count=2)
        b = await same_account(sessions, a, await _seed_reply(sessions, draft_count=2))
        c = await same_account(sessions, a, await _seed_reply(sessions, draft_count=2), "54322")
        qa, qb, qc = [await queue(runtime, sessions, seed) for seed in (a, b, c)]
        ca, cb = await runtime.claim(qa.sequence_id), await runtime.claim(qb.sequence_id)
        assert ca is not None and cb is not None
        assert await runtime.claim(qc.sequence_id) is None
        slow, fast = FakeTelegram(), FakeTelegram()
        slow.release = asyncio.Event()
        pending = asyncio.create_task(dispatch_claim(runtime, slow, ca))
        try:
            await asyncio.wait_for(slow.started.wait(), 3)
            await clear_budget(sessions)
            await asyncio.wait_for(dispatch_claim(runtime, fast, cb), 3)
            assert not pending.done() and fast.calls[0]["chat_id"] == b.recipient_chat_id
        finally:
            slow.release.set()
            await asyncio.wait_for(pending, 3)


@pytest.mark.anyio
async def test_two_fifty_item_series_preserve_full_order_without_real_sleep(monkeypatch):
    from app.infrastructure.reply_sends import SqlTelegramReplySender

    # Ordering uses controlled clocks; the real start deadline has its own HTTP test.
    monkeypatch.setattr(SqlTelegramReplySender, "_dispatch_clock", staticmethod(lambda: 100.0))
    async with runtime_fixture() as (runtime, sessions):
        seeds = [await _seed_reply(sessions, draft_count=50) for _ in range(2)]
        queued = [await queue(runtime, sessions, s) for s in seeds]
        clock = datetime.now(UTC) + timedelta(seconds=1)

        async def now(_session):
            return clock

        monkeypatch.setattr(runtime, "db_now", now)
        telegram = FakeTelegram()
        for position in range(50):
            for q in queued:
                await clear_budget(sessions)
                claim = await runtime.claim(q.sequence_id)
                assert claim is not None and claim.position == position
                calls_before = len(telegram.calls)
                await dispatch_claim(runtime, telegram, claim)
                assert len(telegram.calls) == calls_before + 1, (position, q.sequence_id)
            clock += timedelta(seconds=61)
        assert len(telegram.calls) == 100
        for seed in seeds:
            calls = [
                c
                for c in telegram.calls
                if c["business_connection_id"] == seed.business_connection_id
            ]
            assert [c["text"] for c in calls] == [f"Approved draft {i}" for i in range(1, 51)]
        async with sessions() as session:
            assert (await session.scalars(select(ReplySequence.state))).all() == ["completed"] * 2


@pytest.mark.anyio
async def test_actual_completion_sixty_second_boundary_and_no_catchup(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=3)
        q = await queue(runtime, sessions, seed)
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, await runtime.claim(q.sequence_id))
        async with sessions() as session:
            first = await session.get(ReplySequenceItem, (q.sequence_id, 0))
            clock = first.finished_at + timedelta(seconds=59)

        async def now(_session):
            return clock

        monkeypatch.setattr(runtime, "db_now", now)
        assert await runtime.claim(q.sequence_id) is None
        clock += timedelta(seconds=1)
        claim = await runtime.claim(q.sequence_id)
        assert claim is not None
        await runtime.release_preflight(claim)
        clock += timedelta(minutes=20)
        claim = await runtime.claim(q.sequence_id)
        await dispatch_claim(runtime, telegram, claim)
        assert len(telegram.calls) == 2
        assert await runtime.claim(q.sequence_id) is None
        clock += timedelta(seconds=59)
        assert await runtime.claim(q.sequence_id) is None
        clock += timedelta(seconds=1)
        assert await runtime.claim(q.sequence_id) is not None


@pytest.mark.anyio
async def test_stale_claim_token_cannot_send_or_release_new_owner():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        old = await runtime.claim(q.sequence_id)
        async with sessions() as session:
            past = datetime.now(UTC) - timedelta(seconds=1)
            await session.execute(update(ReplySequence).values(claim_expires_at=past))
            await session.execute(
                update(ReplyDispatchPermit)
                .where(ReplyDispatchPermit.state == "preflight")
                .values(expires_at=past)
            )
            await session.commit()
        new = await runtime.claim(q.sequence_id)
        assert new is not None and new.token != old.token
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, old)
        assert not telegram.calls
        await dispatch_claim(runtime, telegram, new)
        assert len(telegram.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("terminal", ["sent", "failed", "unknown"])
async def test_ledger_projection_crash_recovery_is_idempotent_without_send(monkeypatch, terminal):
    # Reach the ledger/projection crash under test, rather than an unrelated
    # pre-transport expiry when the CI host's reservation commit takes >100 ms.
    monkeypatch.setattr(SqlTelegramReplySender, "_dispatch_clock", staticmethod(lambda: 100.0))
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)
        original = runtime.project_outcome

        async def lost_projection(*_args, **_kwargs):
            pass

        monkeypatch.setattr(runtime, "project_outcome", lost_projection)
        error = (
            None
            if terminal == "sent"
            else (
                TimeoutError()
                if terminal == "unknown"
                else TelegramApiError(error_code=403, http_status=403, description="synthetic")
            )
        )
        telegram = FakeTelegram(error=error)
        await dispatch_claim(runtime, telegram, claim)
        monkeypatch.setattr(runtime, "project_outcome", original)
        await runtime.reconcile()
        await runtime.reconcile()
        assert len(telegram.calls) == 1
        async with sessions() as session:
            sequence = await session.get(ReplySequence, q.sequence_id)
            item = await session.get(ReplySequenceItem, (q.sequence_id, 0))
            assert item.state == terminal
            assert sequence.state == ("active" if terminal == "sent" else "stopped")
            slots = (await session.scalars(select(ReplyDispatchPermit.state))).all()
            assert slots.count("quarantined") == (1 if terminal == "unknown" else 0)
            assert sequence.next_position == (1 if terminal == "sent" else 0)


@pytest.mark.anyio
@pytest.mark.parametrize("elapsed,expected", [(0.05, "sent"), (0.2, "cancelled")])
async def test_reservation_commit_delay_enforces_dispatch_start_window(
    monkeypatch, elapsed, expected
):
    ticks = []

    def clock():
        ticks.append(True)
        return 100.0 if len(ticks) == 1 else 100.0 + elapsed

    monkeypatch.setattr(SqlTelegramReplySender, "_dispatch_clock", staticmethod(clock))
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        queued = await queue(runtime, sessions, seed)
        claim = await runtime.claim(queued.sequence_id)
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert len(ticks) == 2  # admission before commit, then final pre-transport check
        assert len(telegram.calls) == (1 if expected == "sent" else 0)
        async with sessions() as session:
            attempt = (await session.scalars(select(SendAttempt))).one()
            assert attempt.status == expected
            if expected == "cancelled":
                assert attempt.error_code == "dispatch_start_window_expired"


@pytest.mark.anyio
async def test_sending_expiry_becomes_unknown_quarantine_without_retry(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)
        telegram = FakeTelegram()
        original = runtime.project_outcome

        async def crash_after_ledger(*_args, **_kwargs):
            async with sessions() as session:
                await session.execute(
                    update(SendAttempt).values(
                        status="sending",
                        provider_message_id=None,
                        finished_at=None,
                        started_at=datetime.now(UTC) - timedelta(minutes=2),
                    )
                )
                await session.commit()

        monkeypatch.setattr(runtime, "project_outcome", crash_after_ledger)
        await dispatch_claim(runtime, telegram, claim)
        monkeypatch.setattr(runtime, "project_outcome", original)
        await runtime.reconcile()
        assert await runtime.claim(q.sequence_id) is None
        async with sessions() as session:
            assert (await session.scalar(select(SendAttempt.status))) == "unknown"
            assert (
                await session.scalars(
                    select(ReplyDispatchPermit).where(
                        ReplyDispatchPermit.sequence_id == q.sequence_id
                    )
                )
            ).one().state == "quarantined"
        assert len(telegram.calls) == 1


@pytest.mark.anyio
async def test_inbound_during_probe_prevents_admission():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)

        class ProbeTelegram(FakeTelegram):
            async def get_business_connection(self, **kwargs):
                await accept(sessions, inbound(seed))
                return await super().get_business_connection(**kwargs)

        telegram = ProbeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert not telegram.calls
        async with sessions() as session:
            assert not (await session.scalars(select(SendAttempt))).all()


@pytest.mark.anyio
async def test_same_recipient_other_account_is_not_stopped_or_exposed():
    async with runtime_fixture() as (runtime, sessions):
        a, b = [await _seed_reply(sessions, draft_count=2) for _ in range(2)]
        qa, qb = await queue(runtime, sessions, a), await queue(runtime, sessions, b)
        await accept(sessions, inbound(a))
        principal = _command(a, a.draft_ids[0]).principal
        async with sessions() as session:
            assert (await session.get(ReplySequence, qb.sequence_id)).state == "active"
            assert await runtime.detail(session, principal, qb.sequence_id) is None
            detail = await runtime.detail(session, principal, qa.sequence_id, limit=1)
            Draft202012Validator(SEQUENCE_DETAIL_SCHEMA).validate(detail)
            assert sum(detail["counts"].values()) == 2 and len(detail["items"]) == 1
            assert detail["next_cursor"] == "1" and detail["can_auto_resume"] is False
            summary = await runtime.summaries(session, principal)
            Draft202012Validator(SEQUENCE_SUMMARIES_SCHEMA).validate(summary)
            assert summary["counts"] == {"active": 0, "stopped": 1, "completed": 0}
            foreign = replace(
                principal,
                access_scope=AccessScope(
                    workspace_id="foreign",
                    account_ids=principal.access_scope.account_ids,
                    project_ids=principal.access_scope.project_ids,
                ),
            )
            assert await runtime.detail(session, foreign, qa.sequence_id) is None


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["missing_item", "missing_finished", "tail_changed"])
async def test_corrupt_membership_or_approval_never_sends(fault):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        async with sessions() as session:
            if fault == "missing_item":
                await session.execute(
                    delete(ReplySequenceItem).where(ReplySequenceItem.position == 1)
                )
            elif fault == "missing_finished":
                await session.execute(
                    update(ReplySequenceItem)
                    .where(ReplySequenceItem.position == 0)
                    .values(state="sent")
                )
            else:
                await session.execute(
                    update(ReplyDraft)
                    .where(ReplyDraft.id == seed.draft_ids[1])
                    .values(draft_text="changed tail")
                )
            await session.commit()
        telegram = FakeTelegram()
        claim = await runtime.claim(q.sequence_id)
        if claim:
            await dispatch_claim(runtime, telegram, claim)
        await runtime.reconcile()
        assert not telegram.calls
        async with sessions() as session:
            assert (await session.get(ReplySequence, q.sequence_id)).state == "stopped"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "gate", ["disabled", "generation", "caps", "continuity", "restart", "connection_update"]
)
async def test_control_or_ingress_change_blocks_preclaimed_send(gate):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
            if gate == "disabled":
                control.mode = "disabled"
            elif gate == "generation":
                control.generation += 1
            elif gate == "caps":
                control.max_inflight = 1
            elif gate == "continuity":
                connector.continuity_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        if gate == "restart":
            await runtime.invalidate_ingress("ingress_runtime_started")
        elif gate == "connection_update":
            command = inbound(seed)
            await accept(
                sessions,
                replace(
                    command,
                    payload={
                        "update_id": command.update_id,
                        "business_connection": {
                            "id": seed.business_connection_id,
                            "is_enabled": False,
                        },
                    },
                ),
            )
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert not telegram.calls


@pytest.mark.anyio
async def test_shared_manual_document_notification_budget_and_restart_cooldown():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        budget = SqlTelegramTransportBudget(runtime)
        await budget.before({"chat_id": "999"})  # owner notification consumes the bot budget
        assert await runtime.claim(q.sequence_id) is None
        await clear_budget(sessions)
        await budget.before(
            {
                "chat_id": seed.recipient_chat_id,
                "business_connection_id": seed.business_connection_id,
            }
        )
        with pytest.raises(TelegramApiError):
            await budget.before({"chat_id": "999"})
        await budget.failed({"chat_id": "999"}, 120)
        restarted = ReplySequenceRuntime(
            engine=sessions.kw["bind"],
            sessions=sessions,
            settings=runtime.settings,
            sender_guards=sessions.kw["bind"],
            ingress_guards=sessions.kw["bind"],
        )
        assert await restarted.claim(q.sequence_id) is None
        async with sessions() as session:
            assert any(
                b.blocked_until is not None
                for b in (await session.scalars(select(ReplyTransportBudget))).all()
            )


@pytest.mark.anyio
async def test_receipt_stop_transaction_rollback_leaves_no_partial_side_effect(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        original = runtime.inbound

        async def fail(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("synthetic transaction fault")

        monkeypatch.setattr(runtime, "inbound", fail)
        with pytest.raises(RuntimeError):
            await accept(sessions, inbound(seed))
        async with sessions() as session:
            assert (await session.get(ReplySequence, q.sequence_id)).state == "active"
            assert (await session.scalar(select(ReplyDialogGuard.inbound_revision))) == 0
            assert not (await session.scalars(select(ReplySequenceNotification))).all()
        monkeypatch.setattr(runtime, "inbound", original)
        await accept(sessions, inbound(seed))
        assert await runtime.claim(q.sequence_id) is None


@pytest.mark.anyio
async def test_late_confirmed_outcome_keeps_terminal_stop(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)
        original = runtime.project_outcome

        async def lost(*args, **kwargs):
            pass

        monkeypatch.setattr(runtime, "project_outcome", lost)
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        await accept(sessions, inbound(seed))
        monkeypatch.setattr(runtime, "project_outcome", original)
        async with dialog_guard(
            runtime.ingress_guards,
            workspace=runtime.workspace,
            account_id=seed.account_id,
            chat_id=seed.recipient_chat_id,
        ):
            await runtime.project_outcome(claim, coroutine_finished=True)
        async with sessions() as session:
            assert (await session.get(ReplySequence, q.sequence_id)).state == "stopped"
            assert (await session.get(ReplySequenceItem, (q.sequence_id, 0))).state == "sent"
        assert await runtime.claim(q.sequence_id) is None and len(telegram.calls) == 1


@pytest.mark.anyio
async def test_keyset_discovery_survives_ten_thousand_history_and_thousand_blocked_candidates(
    monkeypatch,
):
    from uuid import uuid4

    from sqlalchemy import insert, text

    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        async with sessions() as session:
            sequence = await session.get(ReplySequence, q.sequence_id)
            template = {c.key: getattr(sequence, c.key) for c in ReplySequence.__table__.columns}
            base = datetime.now(UTC) - timedelta(seconds=10)
            guards, active, history = [], [], []
            for n in range(1000):
                guard_id = uuid4()
                guards.append(
                    dict(
                        id=guard_id,
                        workspace_ref=runtime.workspace,
                        account_id=seed.account_id,
                        provider_chat_id=str(20000 + n),
                        inbound_revision=0,
                        health_state="healthy",
                        guard_version=0,
                    )
                )
                active.append(
                    {
                        **template,
                        "id": uuid4(),
                        "guard_id": guard_id,
                        "next_due_at": base,
                        "state": "active",
                    }
                )
            for _ in range(10000):
                history.append({**template, "id": uuid4(), "state": "completed"})
            await session.execute(insert(ReplyDialogGuard), guards)
            await session.execute(insert(ReplySequence), active)
            for offset in range(0, len(history), 500):
                await session.execute(insert(ReplySequence), history[offset : offset + 500])
            await session.commit()
            await session.execute(text("ANALYZE reply_sequences"))
            plan = (
                await session.execute(
                    text(
                        "EXPLAIN (ANALYZE, FORMAT JSON) SELECT id, next_due_at "
                        "FROM reply_sequences "
                        "WHERE state='active' AND workspace_ref=:workspace "
                        "AND next_due_at<=:now "
                        "ORDER BY next_due_at,id LIMIT 100"
                    ),
                    {"workspace": runtime.workspace, "now": await runtime.db_now(session)},
                )
            ).scalar_one()
            assert "ix_reply_sequences_active_due" in str(plan)
        seen = []
        for _ in range(11):
            page = await runtime.candidates()
            assert len(page) <= 100
            seen.extend(page)
        assert len(seen) == 1001 and len(set(seen)) == 1001
        assert seen[-1] == q.sequence_id
        claim = await runtime.claim(q.sequence_id)
        telegram = FakeTelegram()
        # This test measures indexed discovery, not the CI host's fsync latency.
        # Real start-window refusal is covered separately at the HTTP boundary.
        from app.infrastructure.reply_sends import SqlTelegramReplySender

        monkeypatch.setattr(SqlTelegramReplySender, "_dispatch_clock", staticmethod(lambda: 100.0))
        await dispatch_claim(runtime, telegram, claim)
        assert len(telegram.calls) == 1


@pytest.mark.anyio
async def test_concurrent_queue_one_active_and_default_serial_one_global_permit():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        attempts = await asyncio.gather(
            *(queue(runtime, sessions, seed) for _ in range(4)), return_exceptions=True
        )
        assert sum(not isinstance(a, BaseException) for a in attempts) == 1
        assert all(
            not isinstance(a, BaseException) or isinstance(a, ReplySequenceError) for a in attempts
        )
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            control.mode = "serial"
            await session.commit()
            q = await session.scalar(select(ReplySequence.id))
        other = await _seed_reply(sessions, draft_count=2)
        qb = await queue(runtime, sessions, other)
        assert await runtime.claim(q) is not None
        assert await runtime.claim(qb.sequence_id) is None


@pytest.mark.anyio
async def test_expired_preflight_reconciliation_and_shutdown_quarantine_are_durable():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        await runtime.claim(q.sequence_id)
        async with sessions() as session:
            await session.execute(
                update(ReplyDispatchPermit)
                .where(ReplyDispatchPermit.sequence_id == q.sequence_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        await runtime.reconcile()
        assert await runtime.claim(q.sequence_id) is not None
        await runtime.quarantine_shutdown((q.sequence_id,))
        async with sessions() as session:
            assert (await session.get(ReplyDispatchControl, runtime.workspace)).mode == "disabled"
            permit = (
                await session.scalars(
                    select(ReplyDispatchPermit).where(
                        ReplyDispatchPermit.sequence_id == q.sequence_id
                    )
                )
            ).one()
            assert permit.state == "quarantined"
        assert await runtime.claim(q.sequence_id) is None


@pytest.mark.anyio
async def test_concurrent_stop_dispatch_stress_never_starts_after_committed_stop():
    async with runtime_fixture() as (runtime, sessions):
        seeds = [await _seed_reply(sessions, draft_count=2) for _ in range(6)]
        queued = [await queue(runtime, sessions, s) for s in seeds]
        committed = [False] * 6
        calls = []

        async def stop(n):
            await accept(sessions, inbound(seeds[n], update_id=800 + n, message_id=900 + n))
            committed[n] = True

        async def send(n):
            claim = await runtime.claim(queued[n].sequence_id)
            if claim is None:
                return

            class Transport(FakeTelegram):
                async def send_plain_message(self, **payload):
                    assert not committed[n]
                    assert payload["business_connection_id"] == seeds[n].business_connection_id
                    calls.append(n)
                    await asyncio.sleep(0.01)
                    return {"ok": True, "result": {"message_id": 1000 + n}}

            await dispatch_claim(runtime, Transport(), claim)

        async with asyncio.timeout(10):
            await asyncio.gather(*(send(n) for n in range(6)), *(stop(n) for n in range(6)))
        assert len(calls) == len(set(calls))
        async with sessions() as session:
            assert (await session.scalars(select(ReplySequence.state))).all() == ["stopped"] * 6
            assert set((await session.scalars(select(ReplySequenceItem.state))).all()) <= {
                "sent",
                "cancelled",
            }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,expected,calls",
    [
        (None, "sent", 1),
        (TelegramApiError(error_code=403, http_status=403, description="synthetic"), "failed", 1),
        (
            TelegramApiError(
                error_code=429, http_status=429, description="synthetic", retry_after_seconds=5
            ),
            "failed",
            4,
        ),
    ],
)
async def test_notification_success_refusal_and_bounded_known_retry(error, expected, calls):
    from app.models.domain import Account
    from app.worker.reply_sequence_notifications import notify_once

    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        async with sessions() as session:
            account = await session.get(Account, seed.account_id)
            account.permissions = {"reply_sequence_owner_subject": "reply-send-postgres-test"}
            await session.commit()
        q = await queue(runtime, sessions, seed)
        assert q.notification_readiness == "ready"
        await accept(sessions, inbound(seed))
        telegram = FakeTelegram(error=error)
        for _ in range(5):
            await notify_once(runtime, telegram)
            async with sessions() as session:
                event = (await session.scalars(select(ReplySequenceNotification))).one()
                if event.delivery_state == "pending":
                    assert event.next_attempt_at > event.started_at
                    event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
                    await session.commit()
        assert len(telegram.calls) == calls
        assert all(c["chat_id"] == "999" for c in telegram.calls)
        async with sessions() as session:
            assert (
                await session.scalar(select(ReplySequenceNotification.delivery_state))
            ) == expected


@pytest.mark.anyio
async def test_operator_cutover_cannot_reactivate_old_approval_across_ingress_gap(monkeypatch):
    from argparse import Namespace

    from app.infrastructure.database import DatabaseRuntime
    from app.worker import reply_sequence_control as control_cli

    async with runtime_fixture() as (runtime, sessions):
        monkeypatch.setattr(control_cli, "get_settings", lambda: runtime.settings)
        monkeypatch.setattr(
            control_cli,
            "build_database_runtime",
            lambda _url: DatabaseRuntime(sessions.kw["bind"], sessions, runtime),
        )
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        await runtime.invalidate_ingress("synthetic_gap")
        args = Namespace(
            action="serial",
            legacy_workers_drained=True,
            continuity_until=(datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            continuity_evidence_ref="synthetic-continuity-receipt",
        )
        with pytest.raises(ReplySequenceError) as exc:
            await control_cli.execute(args)
        assert exc.value.code == "sequence_continuity_reapproval_required"
        args.action = "stop-active"
        assert (await control_cli.execute(args))["stopped"] == 1
        args.action = "serial"
        assert (await control_cli.execute(args))["mode"] == "serial"
        async with sessions() as session:
            assert (await session.get(ReplySequence, q.sequence_id)).state == "stopped"
        assert await runtime.claim(q.sequence_id) is None


@pytest.mark.anyio
async def test_canonical_membership_cannot_be_bypassed_by_lost_json_projection():
    from app.application.reply_sends import ReplySendError
    from app.infrastructure.reply_sends import SqlTelegramReplySender

    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(runtime, sessions, seed)
        async with sessions() as session:
            await session.execute(
                update(ReplyDraft)
                .where(ReplyDraft.id == seed.draft_ids[0])
                .values(prompt_context={})
            )
            await session.commit()
        telegram = FakeTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as exc:
                await SqlTelegramReplySender(
                    session=session, telegram=telegram, settings=runtime.settings
                ).send(_command(seed, seed.draft_ids[0]))
            assert exc.value.code == "sequence_stopped"
        assert not telegram.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sender,expected", [(123, "active"), (12345, "stopped"), (None, "stopped")]
)
async def test_only_authoritative_gate_bot_echo_is_ignored(sender, expected):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        command = inbound(seed)
        command.payload["business_message"]["from"] = {"id": sender, "is_bot": True}
        await accept(sessions, command)
        async with sessions() as session:
            sequence = await session.get(ReplySequence, q.sequence_id)
            assert sequence.state == expected
            if expected == "stopped":
                assert sequence.stop_reason == "guard_unavailable"
                assert (await session.scalar(select(ReplyDialogGuard.health_state))) == "blocked"


@pytest.mark.anyio
async def test_lost_guard_then_stop_commit_fences_paused_old_http_start(monkeypatch):
    from app.infrastructure.reply_sequence_guard import DialogLease
    from app.services.telegram_delivery_gate import require_reserved_start_window
    from sqlalchemy import text

    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)

        async def lose_barrier_then_resume(lease, effect):
            await lease.connection.scalar(
                text("SELECT pg_advisory_unlock(:key)"), {"key": lease.key}
            )
            await lease.connection.commit()
            await accept(sessions, inbound(seed))
            # The old process resumes only after the new guard owner committed stop.
            return await effect

        monkeypatch.setattr(DialogLease, "transport", lose_barrier_then_resume)

        class PausedTelegram(FakeTelegram):
            async def send_plain_message(self, **payload):
                require_reserved_start_window(runtime.settings.telegram_bot_token)
                return await super().send_plain_message(**payload)

        telegram = PausedTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert not telegram.calls
        async with sessions() as session:
            assert (await session.get(ReplySequence, q.sequence_id)).state == "stopped"
            assert (await session.scalar(select(SendAttempt.status))) == "cancelled"
