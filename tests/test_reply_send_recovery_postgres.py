"""PostgreSQL guards for bounded, database-only reply-send recovery."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.application.reply_send_recovery import (
    RecoverStaleReplySendsCommand,
    ReplySendRecoveryResult,
)
from app.core.config import get_settings
from app.infrastructure.reply_send_recovery import SqlReplySendRecovery
from app.models.domain import (
    Account,
    AuditLog,
    Base,
    Bot,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    Project,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
    SendAttemptStatus,
)
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_AUDIT_ACTION = "reply_send_stale_attempts_recovered"
_PRIVATE_SENTINEL = "must-never-appear-in-recovery-audit"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class _AttemptSpec:
    label: str
    status: str
    created_at: datetime
    attempt_id: UUID
    reserved_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class _SeededAttempt:
    attempt_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    task_id: UUID
    draft_id: UUID


@asynccontextmanager
async def _postgres_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL reply-send recovery tests")

    schema = f"reply_send_recovery_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def _seed_attempts(
    sessions: async_sessionmaker[AsyncSession],
    specs: Sequence[_AttemptSpec],
) -> dict[str, _SeededAttempt]:
    bot_id = uuid4()
    project_id = uuid4()
    account_id = uuid4()
    context_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    seeded: dict[str, _SeededAttempt] = {}

    async with sessions() as session:
        session.add_all(
            [
                Project(
                    id=project_id,
                    alias=f"recovery-project-{project_id.hex}",
                    name=f"Reply recovery test project {project_id.hex}",
                    adapter_type="telegram",
                    is_active=True,
                ),
                Bot(
                    id=bot_id,
                    username=f"gate_recovery_{bot_id.hex}",
                    is_active=True,
                ),
            ]
        )
        await session.flush()
        session.add(
            Account(
                id=account_id,
                alias=f"recovery-account-{account_id.hex}",
                owner="recovery-test-owner",
                country_code="RU",
                telegram_user_id=999,
                account_type="personal",
                linked_project_id=project_id,
                permissions={},
                read_enabled=True,
                send_enabled=True,
                is_active=True,
            )
        )
        await session.flush()

        for index, spec in enumerate(specs):
            conversation_id = uuid4()
            trigger_message_id = uuid4()
            task_id = uuid4()
            draft_id = uuid4()
            peer = f"{_PRIVATE_SENTINEL}-peer-{spec.label}"
            connection = f"{_PRIVATE_SENTINEL}-connection-{spec.label}"
            seeded[spec.label] = _SeededAttempt(
                attempt_id=spec.attempt_id,
                conversation_id=conversation_id,
                trigger_message_id=trigger_message_id,
                task_id=task_id,
                draft_id=draft_id,
            )
            session.add_all(
                [
                    Conversation(
                        id=conversation_id,
                        bot_id=bot_id,
                        account_id=account_id,
                        project_id=project_id,
                        chat_id=peer,
                        business_connection_id=connection,
                        status=ConversationStatus.MUTED.value,
                        requires_reply=True,
                        last_message_at=context_at,
                        last_inbound_at=context_at,
                    ),
                    Message(
                        id=trigger_message_id,
                        bot_id=bot_id,
                        account_id=account_id,
                        project_id=project_id,
                        direction=MessageDirection.INBOUND.value,
                        source="telegram",
                        event_type="business_message",
                        chat_id=peer,
                        sender_id=peer,
                        text=f"{_PRIVATE_SENTINEL}-inbound-{spec.label}",
                        business_connection_id=connection,
                        payload={"private": _PRIVATE_SENTINEL},
                        received_at=context_at,
                    ),
                ]
            )
            if index == 0:
                session.add(
                    Message(
                        id=uuid4(),
                        bot_id=bot_id,
                        account_id=account_id,
                        project_id=project_id,
                        direction=MessageDirection.OUTBOUND.value,
                        source="preexisting-outbound-sentinel",
                        event_type="business_message",
                        chat_id=peer,
                        sender_id="gate",
                        recipient_id=peer,
                        text=f"{_PRIVATE_SENTINEL}-existing-outbound",
                        business_connection_id=connection,
                        payload={"private": _PRIVATE_SENTINEL},
                        sent_at=context_at,
                    )
                )
        await session.flush()

        for item in seeded.values():
            session.add(
                ReplyTask(
                    id=item.task_id,
                    account_id=account_id,
                    project_id=project_id,
                    conversation_id=item.conversation_id,
                    trigger_message_id=item.trigger_message_id,
                    status=ReplyTaskStatus.SNOOZED.value,
                    priority=37,
                    reason=f"{_PRIVATE_SENTINEL}-task-reason",
                )
            )
        await session.flush()

        for item in seeded.values():
            session.add(
                ReplyDraft(
                    id=item.draft_id,
                    account_id=account_id,
                    project_id=project_id,
                    reply_task_id=item.task_id,
                    conversation_id=item.conversation_id,
                    trigger_message_id=item.trigger_message_id,
                    status=ReplyDraftStatus.EDITED.value,
                    source="reply-send-recovery-test",
                    draft_text=f"{_PRIVATE_SENTINEL}-draft-text",
                    prompt_context={"private": _PRIVATE_SENTINEL},
                )
            )
        await session.flush()

        for spec in specs:
            item = seeded[spec.label]
            session.add(
                SendAttempt(
                    id=spec.attempt_id,
                    reply_draft_id=item.draft_id,
                    reply_task_id=item.task_id,
                    conversation_id=item.conversation_id,
                    account_id=account_id,
                    project_id=project_id,
                    principal_subject=f"{_PRIVATE_SENTINEL}-principal",
                    status=spec.status,
                    account_label="RU",
                    target_chat_id=f"{_PRIVATE_SENTINEL}-target-{spec.label}",
                    business_connection_id=(f"{_PRIVATE_SENTINEL}-business-{spec.label}"),
                    provider_message_id=spec.provider_message_id,
                    error_code=spec.error_code,
                    error_detail=spec.error_detail,
                    reserved_at=spec.reserved_at,
                    started_at=spec.started_at,
                    finished_at=spec.finished_at,
                    created_at=spec.created_at,
                    updated_at=spec.created_at,
                )
            )
        await session.commit()

    return seeded


def _spec(
    label: str,
    status: str,
    created_at: datetime,
    *,
    attempt_id: UUID | None = None,
    reserved_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    provider_message_id: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> _AttemptSpec:
    return _AttemptSpec(
        label=label,
        status=status,
        created_at=created_at,
        attempt_id=attempt_id or uuid4(),
        reserved_at=reserved_at or created_at,
        started_at=started_at,
        finished_at=finished_at,
        provider_message_id=provider_message_id,
        error_code=error_code,
        error_detail=error_detail,
    )


async def _recover(
    sessions: async_sessionmaker[AsyncSession],
    command: RecoverStaleReplySendsCommand,
) -> ReplySendRecoveryResult:
    async with sessions() as session:
        return await SqlReplySendRecovery(session).recover_stale(command)


async def _attempt_snapshots(
    sessions: async_sessionmaker[AsyncSession],
) -> dict[UUID, tuple[object, ...]]:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(
                    SendAttempt.id,
                    SendAttempt.status,
                    SendAttempt.provider_message_id,
                    SendAttempt.error_code,
                    SendAttempt.error_detail,
                    SendAttempt.reserved_at,
                    SendAttempt.started_at,
                    SendAttempt.finished_at,
                    SendAttempt.created_at,
                    SendAttempt.principal_subject,
                    SendAttempt.target_chat_id,
                    SendAttempt.business_connection_id,
                    SendAttempt.updated_at,
                )
            )
        ).all()
    return {row.id: tuple(row[1:]) for row in rows}


async def _context_snapshot(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[tuple[object, ...], ...]:
    async with sessions() as session:
        conversations = (
            await session.execute(
                select(
                    Conversation.id,
                    Conversation.status,
                    Conversation.requires_reply,
                    Conversation.last_message_at,
                    Conversation.last_inbound_at,
                ).order_by(Conversation.id)
            )
        ).all()
        tasks = (
            await session.execute(
                select(
                    ReplyTask.id,
                    ReplyTask.status,
                    ReplyTask.priority,
                    ReplyTask.reason,
                ).order_by(ReplyTask.id)
            )
        ).all()
        drafts = (
            await session.execute(
                select(
                    ReplyDraft.id,
                    ReplyDraft.status,
                    ReplyDraft.source,
                    ReplyDraft.draft_text,
                    ReplyDraft.prompt_context,
                ).order_by(ReplyDraft.id)
            )
        ).all()
        messages = (
            await session.execute(
                select(
                    Message.id,
                    Message.direction,
                    Message.source,
                    Message.text,
                    Message.payload,
                    Message.sent_at,
                ).order_by(Message.id)
            )
        ).all()
    return tuple(tuple(row) for group in (conversations, tasks, drafts, messages) for row in group)


@pytest.mark.anyio
async def test_stale_active_attempts_recover_exactly_and_leave_other_state_untouched() -> None:
    async with _postgres_sessions() as sessions:
        recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        stale_before = recovered_at - timedelta(minutes=15)
        stale_at = stale_before - timedelta(seconds=1)
        fresh_at = stale_before + timedelta(seconds=1)
        terminal_at = stale_before - timedelta(hours=1)
        terminal_finished_at = terminal_at + timedelta(seconds=5)
        specs = [
            _spec(
                "stale-sending",
                SendAttemptStatus.SENDING.value,
                stale_at,
                started_at=stale_at,
                error_code="superseded-sending-code",
                error_detail=f"{_PRIVATE_SENTINEL}-superseded-sending-detail",
            ),
            _spec(
                "stale-reserved",
                SendAttemptStatus.RESERVED.value,
                stale_at,
                error_code="superseded-reserved-code",
                error_detail=f"{_PRIVATE_SENTINEL}-superseded-reserved-detail",
            ),
            _spec(
                "fresh-sending",
                SendAttemptStatus.SENDING.value,
                terminal_at,
                started_at=fresh_at,
                error_code="fresh-code-sentinel",
                error_detail="fresh-detail-sentinel",
            ),
            _spec(
                "fresh-reserved",
                SendAttemptStatus.RESERVED.value,
                terminal_at,
                reserved_at=fresh_at,
                error_code="fresh-reserved-code-sentinel",
                error_detail="fresh-reserved-detail-sentinel",
            ),
            _spec(
                "sent",
                SendAttemptStatus.SENT.value,
                terminal_at,
                started_at=terminal_at,
                finished_at=terminal_finished_at,
                provider_message_id="provider-message-sentinel",
            ),
            _spec(
                "failed",
                SendAttemptStatus.FAILED.value,
                terminal_at,
                started_at=terminal_at,
                finished_at=terminal_finished_at,
                error_code="terminal-failed-code",
                error_detail="terminal-failed-detail",
            ),
            _spec(
                "unknown",
                SendAttemptStatus.UNKNOWN.value,
                terminal_at,
                started_at=terminal_at,
                finished_at=terminal_finished_at,
                error_code="existing-unknown-code",
                error_detail="existing-unknown-detail",
            ),
            _spec(
                "cancelled",
                SendAttemptStatus.CANCELLED.value,
                terminal_at,
                finished_at=terminal_finished_at,
                error_code="existing-cancelled-code",
                error_detail="existing-cancelled-detail",
            ),
        ]
        seeded = await _seed_attempts(sessions, specs)
        before_attempts = await _attempt_snapshots(sessions)
        before_context = await _context_snapshot(sessions)
        command = RecoverStaleReplySendsCommand(
            stale_before=stale_before,
            recovered_at=recovered_at,
            limit=100,
        )

        result = await _recover(sessions, command)

        assert result.recovered == 2
        assert result.sending_marked_unknown == 1
        assert result.reserved_cancelled == 1
        after_attempts = await _attempt_snapshots(sessions)

        stale_sending = after_attempts[seeded["stale-sending"].attempt_id]
        assert stale_sending[0] == SendAttemptStatus.UNKNOWN.value
        assert stale_sending[1] is None
        assert stale_sending[2] == "delivery_outcome_unknown_after_recovery"
        assert stale_sending[3] is None
        assert stale_sending[6] == recovered_at
        assert stale_sending[4:6] == before_attempts[seeded["stale-sending"].attempt_id][4:6]
        assert stale_sending[7:11] == before_attempts[seeded["stale-sending"].attempt_id][7:11]
        assert stale_sending[11] != before_attempts[seeded["stale-sending"].attempt_id][11]

        stale_reserved = after_attempts[seeded["stale-reserved"].attempt_id]
        assert stale_reserved[0] == SendAttemptStatus.CANCELLED.value
        assert stale_reserved[1] is None
        assert stale_reserved[2] == "reservation_expired_before_delivery"
        assert stale_reserved[3] is None
        assert stale_reserved[5] is None
        assert stale_reserved[6] == recovered_at
        assert stale_reserved[4:6] == before_attempts[seeded["stale-reserved"].attempt_id][4:6]
        assert stale_reserved[7:11] == before_attempts[seeded["stale-reserved"].attempt_id][7:11]
        assert stale_reserved[11] != before_attempts[seeded["stale-reserved"].attempt_id][11]

        untouched_labels = {
            "fresh-sending",
            "fresh-reserved",
            "sent",
            "failed",
            "unknown",
            "cancelled",
        }
        for label in untouched_labels:
            attempt_id = seeded[label].attempt_id
            assert after_attempts[attempt_id] == before_attempts[attempt_id]
        assert await _context_snapshot(sessions) == before_context

        async with sessions() as session:
            audits = (
                (await session.execute(select(AuditLog).where(AuditLog.action == _AUDIT_ACTION)))
                .scalars()
                .all()
            )
        assert len(audits) == 1
        audit = audits[0]
        assert audit.actor == "gate-reply-send-recovery"
        assert audit.bot_id is None
        assert audit.account_id is None
        assert audit.project_id is None
        assert audit.gate_event_id is None
        assert audit.metadata_ == {
            "recovered": 2,
            "sending_marked_unknown": 1,
            "reserved_cancelled": 1,
            "stale_after_seconds": 900,
            "limit": 100,
        }
        assert set(audit.metadata_) == {
            "recovered",
            "sending_marked_unknown",
            "reserved_cancelled",
            "stale_after_seconds",
            "limit",
        }
        redacted_audit = json.dumps(
            {
                "actor": audit.actor,
                "action": audit.action,
                "metadata": audit.metadata_,
            },
            sort_keys=True,
        )
        assert _PRIVATE_SENTINEL not in redacted_audit
        for item in seeded.values():
            assert str(item.attempt_id) not in redacted_audit
            assert str(item.conversation_id) not in redacted_audit
            assert str(item.task_id) not in redacted_audit
            assert str(item.draft_id) not in redacted_audit

        second = await _recover(sessions, command)
        assert second.recovered == 0
        assert second.sending_marked_unknown == 0
        assert second.reserved_cancelled == 0
        async with sessions() as session:
            audit_count = len(
                (
                    await session.execute(
                        select(AuditLog.id).where(AuditLog.action == _AUDIT_ACTION)
                    )
                ).all()
            )
        assert audit_count == 1


@pytest.mark.anyio
async def test_recovery_limit_selects_deterministic_oldest_attempts() -> None:
    async with _postgres_sessions() as sessions:
        recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        stale_before = recovered_at - timedelta(minutes=15)
        oldest = stale_before - timedelta(minutes=4)
        second_oldest = stale_before - timedelta(minutes=3)
        newest = stale_before - timedelta(minutes=2)
        first_id = UUID("00000000-0000-0000-0000-000000000001")
        second_id = UUID("00000000-0000-0000-0000-000000000002")
        seeded = await _seed_attempts(
            sessions,
            [
                _spec(
                    "tie-first",
                    SendAttemptStatus.RESERVED.value,
                    oldest,
                    attempt_id=first_id,
                ),
                _spec(
                    "tie-second",
                    SendAttemptStatus.SENDING.value,
                    oldest,
                    attempt_id=second_id,
                    started_at=oldest,
                ),
                _spec("next", SendAttemptStatus.RESERVED.value, second_oldest),
                _spec(
                    "newest",
                    SendAttemptStatus.SENDING.value,
                    newest,
                    started_at=newest,
                ),
            ],
        )

        result = await _recover(
            sessions,
            RecoverStaleReplySendsCommand(
                stale_before=stale_before,
                recovered_at=recovered_at,
                limit=2,
            ),
        )

        assert result.recovered == 2
        assert result.sending_marked_unknown == 1
        assert result.reserved_cancelled == 1
        snapshots = await _attempt_snapshots(sessions)
        assert snapshots[first_id][0] == SendAttemptStatus.CANCELLED.value
        assert snapshots[second_id][0] == SendAttemptStatus.UNKNOWN.value
        assert snapshots[seeded["next"].attempt_id][0] == SendAttemptStatus.RESERVED.value
        assert snapshots[seeded["newest"].attempt_id][0] == SendAttemptStatus.SENDING.value


@pytest.mark.anyio
async def test_locked_attempt_is_skipped_and_concurrent_recoveries_are_disjoint() -> None:
    async with _postgres_sessions() as sessions:
        recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        stale_before = recovered_at - timedelta(minutes=15)
        old = stale_before - timedelta(minutes=5)
        locked_id = UUID("00000000-0000-0000-0000-000000000010")
        next_id = UUID("00000000-0000-0000-0000-000000000011")
        await _seed_attempts(
            sessions,
            [
                _spec(
                    "locked",
                    SendAttemptStatus.SENDING.value,
                    old,
                    attempt_id=locked_id,
                    started_at=old,
                ),
                _spec(
                    "next",
                    SendAttemptStatus.RESERVED.value,
                    old + timedelta(seconds=1),
                    attempt_id=next_id,
                ),
            ],
        )
        command = RecoverStaleReplySendsCommand(
            stale_before=stale_before,
            recovered_at=recovered_at,
            limit=1,
        )

        async with sessions() as holder:
            locked_attempt = (
                await holder.execute(
                    select(SendAttempt).where(SendAttempt.id == locked_id).with_for_update()
                )
            ).scalar_one()
            async with sessions() as recovery_session:
                await recovery_session.execute(text("SET LOCAL lock_timeout = '750ms'"))
                skipped = await SqlReplySendRecovery(recovery_session).recover_stale(command)
            assert skipped.recovered == 1
            assert skipped.reserved_cancelled == 1
            assert skipped.sending_marked_unknown == 0
            locked_attempt.status = SendAttemptStatus.SENT.value
            locked_attempt.provider_message_id = "provider-confirmed-while-recovery-skipped"
            locked_attempt.error_code = None
            locked_attempt.error_detail = None
            locked_attempt.finished_at = recovered_at
            await holder.commit()

        snapshots = await _attempt_snapshots(sessions)
        assert snapshots[locked_id][0] == SendAttemptStatus.SENT.value
        assert snapshots[locked_id][1] == "provider-confirmed-while-recovery-skipped"
        assert snapshots[next_id][0] == SendAttemptStatus.CANCELLED.value
        after_terminal_commit = await _recover(sessions, command)
        assert after_terminal_commit.recovered == 0
        assert after_terminal_commit.sending_marked_unknown == 0
        assert after_terminal_commit.reserved_cancelled == 0

        concurrent_specs = [
            _spec(
                f"concurrent-{index}",
                (
                    SendAttemptStatus.SENDING.value
                    if index % 2 == 0
                    else SendAttemptStatus.RESERVED.value
                ),
                old + timedelta(seconds=10 + index),
                started_at=(old + timedelta(seconds=10 + index) if index % 2 == 0 else None),
            )
            for index in range(6)
        ]
        concurrent = await _seed_attempts(sessions, concurrent_specs)
        concurrent_command = RecoverStaleReplySendsCommand(
            stale_before=stale_before,
            recovered_at=recovered_at + timedelta(seconds=1),
            limit=3,
        )

        first, second = await asyncio.gather(
            _recover(sessions, concurrent_command),
            _recover(sessions, concurrent_command),
        )

        assert sorted((first.recovered, second.recovered)) == [3, 3]
        assert first.sending_marked_unknown + second.sending_marked_unknown == 3
        assert first.reserved_cancelled + second.reserved_cancelled == 3
        snapshots = await _attempt_snapshots(sessions)
        assert {snapshots[item.attempt_id][0] for item in concurrent.values()} == {
            SendAttemptStatus.UNKNOWN.value,
            SendAttemptStatus.CANCELLED.value,
        }


@pytest.mark.anyio
async def test_audit_flush_failure_rolls_back_the_entire_recovery_batch() -> None:
    async with _postgres_sessions() as sessions:
        recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        stale_before = recovered_at - timedelta(minutes=15)
        stale_at = stale_before - timedelta(seconds=1)
        await _seed_attempts(
            sessions,
            [
                _spec(
                    "sending",
                    SendAttemptStatus.SENDING.value,
                    stale_at,
                    started_at=stale_at,
                ),
                _spec("reserved", SendAttemptStatus.RESERVED.value, stale_at),
            ],
        )
        before = await _attempt_snapshots(sessions)

        def fail_audit_flush(
            sync_session: Any,
            _flush_context: Any,
            _instances: Any,
        ) -> None:
            if any(
                isinstance(instance, AuditLog) and instance.action == _AUDIT_ACTION
                for instance in sync_session.new
            ):
                raise RuntimeError("injected reply-send recovery audit flush failure")

        async with sessions() as session:
            event.listen(session.sync_session, "before_flush", fail_audit_flush)
            try:
                with pytest.raises(
                    RuntimeError,
                    match="injected reply-send recovery audit flush failure",
                ):
                    await SqlReplySendRecovery(session).recover_stale(
                        RecoverStaleReplySendsCommand(
                            stale_before=stale_before,
                            recovered_at=recovered_at,
                            limit=100,
                        )
                    )
            finally:
                event.remove(session.sync_session, "before_flush", fail_audit_flush)
                await session.rollback()

        assert await _attempt_snapshots(sessions) == before
        async with sessions() as session:
            audits = (
                await session.execute(select(AuditLog.id).where(AuditLog.action == _AUDIT_ACTION))
            ).all()
        assert audits == []
