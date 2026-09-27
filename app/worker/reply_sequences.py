"""Disabled-by-default durable worker for approved reply sequences."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.reply_sends import (
    ReplySendError,
    SendApprovedReplyCommand,
    SendApprovedReplyService,
)
from app.application.worker_health import REPLY_SEQUENCE_WORKER, WorkerHealthReporter
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SEQUENCE_CONTEXT_KEY
from app.models.domain import (
    AuditLog,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
    SendAttemptStatus,
)
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SequenceRow:
    """Immutable discovery projection that is safe after the DB session closes."""

    draft_id: UUID
    task_id: UUID
    conversation_id: UUID
    sequence_id: str
    position: int
    state: str
    scheduled_at: datetime
    interval_seconds: int
    account_id: UUID
    project_id: UUID
    expected_account: str
    expected_recipient_chat_id: str
    principal_subject: str
    workspace_id: str
    attempt_status: str | None
    attempt_finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class _DueSequenceItem:
    draft_id: UUID
    task_id: UUID
    conversation_id: UUID
    sequence_id: str
    position: int
    account_id: UUID
    project_id: UUID
    expected_account: str
    expected_recipient_chat_id: str
    principal_subject: str
    workspace_id: str


async def run_reply_sequence_once(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    now: datetime | None = None,
) -> int:
    """Deliver at most one due draft per sequence and return the sent count."""

    if (
        settings.gate_reply_sequence_dispatch_v2_enabled
        or settings.gate_reply_sequence_stop_on_inbound_enabled
    ):
        raise RuntimeError("Legacy sequence iteration is disabled by the v2 guard")
    current_time = now or datetime.now(UTC)
    rows = await _discover_sequence_rows(session_factory)

    due = _due_items(rows, now=current_time)
    sent_count = 0
    for item in due:
        if await _sequence_superseded(
            session_factory=session_factory,
            item=item,
        ):
            await _record_sequence_outcome(
                session_factory=session_factory,
                item=item,
                state="stopped",
                error_code="task_superseded",
                stop_remaining=True,
            )
            continue
        principal = Principal(
            subject=item.principal_subject,
            auth_method="approved-reply-sequence",
            scopes=frozenset({"gate:messages:send"}),
            access_scope=AccessScope(
                workspace_id=item.workspace_id,
                account_ids=frozenset({item.account_id}),
                project_ids=frozenset({item.project_id}),
            ),
        )
        async with session_factory() as send_session:
            try:
                result = await SendApprovedReplyService(
                    SqlTelegramReplySender(
                        session=send_session,
                        telegram=telegram,
                        settings=settings,
                    )
                ).execute(
                    SendApprovedReplyCommand(
                        principal=principal,
                        draft_id=item.draft_id,
                        confirmed=True,
                        expected_account=item.expected_account,
                        expected_recipient_chat_id=item.expected_recipient_chat_id,
                    )
                )
            except ReplySendError as exc:
                await _record_sequence_outcome(
                    session_factory=session_factory,
                    item=item,
                    state="stopped",
                    error_code=exc.code,
                    stop_remaining=True,
                )
                continue

        await _record_sequence_outcome(
            session_factory=session_factory,
            item=item,
            state="sent" if result.sent else result.status,
            attempt_id=result.attempt_id,
            error_code=result.error_code,
            stop_remaining=not result.sent,
        )
        if result.sent:
            sent_count += 1
    return sent_count


async def _discover_sequence_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[_SequenceRow, ...]:
    """Return scalar snapshots only after the discovery session has been closed."""

    rows: list[_SequenceRow] = []
    async with session_factory() as session:
        result = await session.execute(
            select(
                ReplyDraft.id.label("draft_id"),
                ReplyDraft.reply_task_id.label("task_id"),
                ReplyDraft.conversation_id.label("conversation_id"),
                ReplyDraft.account_id.label("account_id"),
                ReplyDraft.project_id.label("project_id"),
                ReplyDraft.prompt_context.label("prompt_context"),
                SendAttempt.status.label("attempt_status"),
                SendAttempt.finished_at.label("attempt_finished_at"),
            )
            .outerjoin(SendAttempt, SendAttempt.reply_draft_id == ReplyDraft.id)
            .where(ReplyDraft.prompt_context.has_key(SEQUENCE_CONTEXT_KEY))  # noqa: E711
            .order_by(ReplyDraft.created_at, ReplyDraft.id)
            .limit(500)
        )
        for raw in result.mappings():
            row = _sequence_row_from_scalars(
                draft_id=raw["draft_id"],
                task_id=raw["task_id"],
                conversation_id=raw["conversation_id"],
                account_id=raw["account_id"],
                project_id=raw["project_id"],
                prompt_context=raw["prompt_context"],
                attempt_status=raw["attempt_status"],
                attempt_finished_at=raw["attempt_finished_at"],
            )
            if row is not None:
                rows.append(row)
        await session.rollback()
    return tuple(rows)


def _sequence_row_from_scalars(
    *,
    draft_id: UUID,
    task_id: UUID,
    conversation_id: UUID,
    account_id: UUID,
    project_id: UUID,
    prompt_context: Mapping[str, Any] | None,
    attempt_status: str | None,
    attempt_finished_at: datetime | None,
) -> _SequenceRow | None:
    """Convert JSON/scalars to a fully detached immutable sequence projection."""

    if prompt_context is None:
        return None
    metadata = prompt_context.get(SEQUENCE_CONTEXT_KEY)
    if not isinstance(metadata, Mapping):
        return None
    try:
        sequence_id = metadata["id"]
        if not isinstance(sequence_id, str) or not sequence_id:
            return None
        scheduled_at = datetime.fromisoformat(str(metadata["scheduled_at"]))
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        state = metadata["state"]
        if not isinstance(state, str):
            return None
        return _SequenceRow(
            draft_id=draft_id,
            task_id=task_id,
            conversation_id=conversation_id,
            sequence_id=sequence_id,
            position=_metadata_int(metadata["position"]),
            state=state,
            scheduled_at=scheduled_at,
            interval_seconds=_metadata_int(metadata["interval_seconds"]),
            account_id=account_id,
            project_id=project_id,
            expected_account=str(metadata["expected_account"]),
            expected_recipient_chat_id=str(metadata["expected_recipient_chat_id"]),
            principal_subject=str(metadata["principal_subject"]),
            workspace_id=str(metadata["workspace_id"]),
            attempt_status=attempt_status,
            attempt_finished_at=attempt_finished_at,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _due_items(
    rows: Sequence[_SequenceRow],
    *,
    now: datetime,
) -> tuple[_DueSequenceItem, ...]:
    groups: dict[str, list[_SequenceRow]] = {}
    for row in rows:
        groups.setdefault(row.sequence_id, []).append(row)

    due: list[_DueSequenceItem] = []
    for sequence_id, sequence_rows in groups.items():
        ordered = sorted(sequence_rows, key=lambda row: row.position)
        candidate: _SequenceRow | None = None
        previous_finished_at: datetime | None = None
        blocked = False
        for row in ordered:
            if row.attempt_status is not None:
                if (
                    row.attempt_status != SendAttemptStatus.SENT.value
                    or row.attempt_finished_at is None
                ):
                    blocked = True
                    break
                previous_finished_at = row.attempt_finished_at
                continue
            if row.state != "scheduled":
                blocked = True
                break
            candidate = row
            break
        if blocked or candidate is None:
            continue
        earliest = candidate.scheduled_at
        if previous_finished_at is not None:
            earliest = max(
                earliest,
                previous_finished_at + timedelta(seconds=candidate.interval_seconds),
            )
        if now < earliest:
            continue
        due.append(
            _DueSequenceItem(
                draft_id=candidate.draft_id,
                task_id=candidate.task_id,
                conversation_id=candidate.conversation_id,
                sequence_id=sequence_id,
                position=candidate.position,
                account_id=candidate.account_id,
                project_id=candidate.project_id,
                expected_account=candidate.expected_account,
                expected_recipient_chat_id=candidate.expected_recipient_chat_id,
                principal_subject=candidate.principal_subject,
                workspace_id=candidate.workspace_id,
            )
        )
    return tuple(due)


def _metadata_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Boolean sequence metadata is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError("Sequence metadata is not an integer")


async def _sequence_superseded(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    item: _DueSequenceItem,
) -> bool:
    async with session_factory() as session:
        current_created_at = await session.scalar(
            select(ReplyTask.created_at).where(ReplyTask.id == item.task_id)
        )
        if current_created_at is None:
            await session.rollback()
            return True
        newer = await session.scalar(
            select(ReplyTask.id)
            .where(
                ReplyTask.conversation_id == item.conversation_id,
                ReplyTask.account_id == item.account_id,
                ReplyTask.project_id == item.project_id,
                ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                ReplyTask.id != item.task_id,
                ReplyTask.created_at > current_created_at,
            )
            .order_by(ReplyTask.created_at, ReplyTask.id)
            .limit(1)
        )
        await session.rollback()
        return newer is not None


async def _record_sequence_outcome(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    item: _DueSequenceItem,
    state: str,
    attempt_id: UUID | None = None,
    error_code: str | None = None,
    stop_remaining: bool,
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(ReplyDraft).where(ReplyDraft.account_id == item.account_id).with_for_update()
        )
        drafts = result.scalars().all()
        for draft in drafts:
            context = dict(draft.prompt_context or {})
            metadata = context.get(SEQUENCE_CONTEXT_KEY)
            if not isinstance(metadata, dict) or metadata.get("id") != item.sequence_id:
                continue
            metadata = dict(metadata)
            position = metadata.get("position")
            if draft.id == item.draft_id:
                metadata["state"] = state
                metadata["finished_at"] = datetime.now(UTC).isoformat()
                if attempt_id is not None:
                    metadata["attempt_id"] = str(attempt_id)
                if error_code:
                    metadata["error_code"] = error_code
            elif stop_remaining and isinstance(position, int) and position > item.position:
                if metadata.get("state") == "scheduled":
                    metadata["state"] = "cancelled"
                    metadata["error_code"] = error_code or "prior_sequence_send_failed"
            context[SEQUENCE_CONTEXT_KEY] = metadata
            draft.prompt_context = context
        session.add(
            AuditLog(
                actor=f"worker:{item.principal_subject}"[:255],
                action=f"reply_sequence_item_{state}",
                account_id=item.account_id,
                project_id=item.project_id,
                metadata_={
                    "sequence_id": item.sequence_id,
                    "draft_id": str(item.draft_id),
                    "position": item.position,
                    "attempt_id": str(attempt_id) if attempt_id else None,
                    "error_code": error_code,
                    "remaining_cancelled": stop_remaining,
                },
            )
        )
        await session.commit()


async def run_reply_sequence_worker_loop(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    health_reporter: WorkerHealthReporter | None = None,
) -> None:
    """Poll durable schedules; cancellation is owned by the application lifespan."""

    if settings.gate_reply_sequence_dispatch_v2_enabled:
        from app.infrastructure.reply_sequence_runtime import runtime_for_session
        from app.worker.reply_sequence_dispatch import run_dispatch_loop

        async with session_factory() as session:
            runtime = runtime_for_session(session)
        if runtime is None:
            raise RuntimeError("Sequence v2 runtime is not initialized")
        await run_dispatch_loop(runtime, telegram, health_reporter=health_reporter)
        return
    while True:
        try:
            sent = await run_reply_sequence_once(
                settings=settings,
                session_factory=session_factory,
                telegram=telegram,
            )
            if health_reporter is not None:
                health_reporter.record_success(
                    REPLY_SEQUENCE_WORKER,
                    observed_at=datetime.now(UTC),
                )
            if sent:
                logger.info("Reply-sequence worker delivered %s message(s)", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            if health_reporter is not None:
                health_reporter.record_failure(
                    REPLY_SEQUENCE_WORKER,
                    error_code="reply_sequence_iteration_failed",
                    observed_at=datetime.now(UTC),
                )
            logger.exception("Reply-sequence worker iteration failed safely")
        await asyncio.sleep(settings.gate_reply_sequence_poll_seconds)
