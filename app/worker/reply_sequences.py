"""Disabled-by-default durable worker for approved reply sequences."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.reply_sends import (
    ReplySendError,
    SendApprovedReplyCommand,
    SendApprovedReplyService,
)
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

    current_time = now or datetime.now(UTC)
    async with session_factory() as discovery_session:
        rows = (
            await discovery_session.execute(
                select(ReplyDraft, SendAttempt)
                .outerjoin(SendAttempt, SendAttempt.reply_draft_id == ReplyDraft.id)
                .where(ReplyDraft.prompt_context.has_key(SEQUENCE_CONTEXT_KEY))  # noqa: E711
                .order_by(ReplyDraft.created_at, ReplyDraft.id)
                .limit(500)
            )
        ).all()
        await discovery_session.rollback()

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


def _due_items(
    rows: list[tuple[ReplyDraft, SendAttempt | None]],
    *,
    now: datetime,
) -> tuple[_DueSequenceItem, ...]:
    groups: dict[str, list[tuple[ReplyDraft, SendAttempt | None, dict[str, object]]]] = {}
    for draft, attempt in rows:
        metadata = (draft.prompt_context or {}).get(SEQUENCE_CONTEXT_KEY)
        if not isinstance(metadata, dict):
            continue
        sequence_id = metadata.get("id")
        if isinstance(sequence_id, str) and sequence_id:
            groups.setdefault(sequence_id, []).append((draft, attempt, metadata))

    due: list[_DueSequenceItem] = []
    for sequence_id, sequence_rows in groups.items():
        try:
            ordered = sorted(sequence_rows, key=lambda row: int(row[2]["position"]))
        except (KeyError, TypeError, ValueError):
            continue
        candidate: tuple[ReplyDraft, SendAttempt | None, dict[str, object]] | None = None
        previous_attempt: SendAttempt | None = None
        blocked = False
        for draft, attempt, metadata in ordered:
            if attempt is not None:
                if attempt.status != SendAttemptStatus.SENT.value:
                    blocked = True
                    break
                previous_attempt = attempt
                continue
            if metadata.get("state") != "scheduled":
                blocked = True
                break
            candidate = (draft, attempt, metadata)
            break
        if blocked or candidate is None:
            continue
        draft, _attempt, metadata = candidate
        try:
            scheduled_at = datetime.fromisoformat(str(metadata["scheduled_at"]))
            interval_seconds = int(metadata["interval_seconds"])
            position = int(metadata["position"])
            expected_account = str(metadata["expected_account"])
            expected_recipient = str(metadata["expected_recipient_chat_id"])
            principal_subject = str(metadata["principal_subject"])
            workspace_id = str(metadata["workspace_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        earliest = scheduled_at
        if previous_attempt is not None:
            if previous_attempt.finished_at is None:
                continue
            earliest = max(
                earliest,
                previous_attempt.finished_at + timedelta(seconds=interval_seconds),
            )
        if now < earliest:
            continue
        due.append(
            _DueSequenceItem(
                draft_id=draft.id,
                task_id=draft.reply_task_id,
                conversation_id=draft.conversation_id,
                sequence_id=sequence_id,
                position=position,
                account_id=draft.account_id,
                project_id=draft.project_id,
                expected_account=expected_account,
                expected_recipient_chat_id=expected_recipient,
                principal_subject=principal_subject,
                workspace_id=workspace_id,
            )
        )
    return tuple(due)


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
            select(ReplyDraft)
            .where(ReplyDraft.account_id == item.account_id)
            .with_for_update()
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
) -> None:
    """Poll durable schedules; cancellation is owned by the application lifespan."""

    while True:
        try:
            sent = await run_reply_sequence_once(
                settings=settings,
                session_factory=session_factory,
                telegram=telegram,
            )
            if sent:
                logger.info("Reply-sequence worker delivered %s message(s)", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reply-sequence worker iteration failed safely")
        await asyncio.sleep(settings.gate_reply_sequence_poll_seconds)
