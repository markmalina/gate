"""Application contract for one explicitly approved sequence of saved replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.mcp_security import Principal

QUEUE_SEQUENCE_SCOPE = "gate:messages:send"
SEQUENCE_INTERVAL_SECONDS = 60
MIN_SEQUENCE_DRAFTS = 2
MAX_SEQUENCE_DRAFTS = 20


class ReplySequenceError(Exception):
    """Expected queueing error safe to expose through MCP."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class QueueApprovedReplySequenceCommand:
    """One explicit approval covering an ordered set of persisted drafts."""

    principal: Principal
    draft_ids: tuple[UUID, ...]
    confirmed: bool
    expected_account: str
    expected_recipient_chat_id: str
    interval_seconds: int = SEQUENCE_INTERVAL_SECONDS


@dataclass(frozen=True, slots=True)
class QueuedReplySequenceItem:
    draft_id: UUID
    position: int
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class QueuedReplySequence:
    sequence_id: UUID
    task_id: UUID
    account_id: UUID
    project_id: UUID
    account_alias: str
    account_label: str
    recipient_chat_id: str
    interval_seconds: int
    items: tuple[QueuedReplySequenceItem, ...]


class ReplySequenceWriter(Protocol):
    async def queue(
        self, command: QueueApprovedReplySequenceCommand
    ) -> QueuedReplySequence: ...


class QueueApprovedReplySequenceService:
    """Validate a single owner confirmation before scheduling external effects."""

    def __init__(self, writer: ReplySequenceWriter) -> None:
        self._writer = writer

    async def execute(
        self, command: QueueApprovedReplySequenceCommand
    ) -> QueuedReplySequence:
        principal = command.principal
        if QUEUE_SEQUENCE_SCOPE not in principal.scopes:
            raise ReplySequenceError(
                code="insufficient_scope",
                message=f"Scope {QUEUE_SEQUENCE_SCOPE} is required",
            )
        if not principal.access_scope.is_explicit:
            raise ReplySequenceError(
                code="invalid_access_scope",
                message="Explicit workspace, account, and project grants are required",
            )
        if command.confirmed is not True:
            raise ReplySequenceError(
                code="confirmation_required",
                message="Explicit confirmation of the complete ordered sequence is required",
            )
        if not MIN_SEQUENCE_DRAFTS <= len(command.draft_ids) <= MAX_SEQUENCE_DRAFTS:
            raise ReplySequenceError(
                code="invalid_sequence",
                message=(
                    f"A reply sequence must contain {MIN_SEQUENCE_DRAFTS} to "
                    f"{MAX_SEQUENCE_DRAFTS} drafts"
                ),
            )
        if len(set(command.draft_ids)) != len(command.draft_ids):
            raise ReplySequenceError(
                code="invalid_sequence",
                message="A reply sequence cannot contain duplicate drafts",
            )
        if command.interval_seconds != SEQUENCE_INTERVAL_SECONDS:
            raise ReplySequenceError(
                code="invalid_interval",
                message=f"Reply sequences require a {SEQUENCE_INTERVAL_SECONDS}-second interval",
            )
        account = command.expected_account.strip().upper()
        if account not in {"RU", "BY"}:
            raise ReplySequenceError(
                code="invalid_confirmation",
                message="Expected account must be RU or BY",
            )
        recipient = command.expected_recipient_chat_id.strip()
        if not recipient:
            raise ReplySequenceError(
                code="invalid_confirmation",
                message="Expected recipient chat id is required",
            )
        return await self._writer.queue(
            QueueApprovedReplySequenceCommand(
                principal=principal,
                draft_ids=command.draft_ids,
                confirmed=True,
                expected_account=account,
                expected_recipient_chat_id=recipient,
                interval_seconds=SEQUENCE_INTERVAL_SECONDS,
            )
        )
