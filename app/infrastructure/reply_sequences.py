"""PostgreSQL persistence for explicitly approved reply sequences."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.application.account_labels import account_label
from app.application.dialog_snapshots import SNAPSHOT_KEY
from app.application.reply_sends import ReplySendError
from app.application.reply_sequences import (
    QueueApprovedReplySequenceCommand,
    QueuedReplySequence,
    QueuedReplySequenceItem,
    ReplySequenceError,
    validate_sequence_draft_ids,
    validate_sequence_payload_digests,
)
from app.application.telegram_formatting import FormattingError, stored_preview
from app.infrastructure.dialog_snapshots import require_saved_dialog_snapshot
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    Message,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    SendAttempt,
)

SEQUENCE_CONTEXT_KEY = "reply_sequence"
_QUEUEABLE_DRAFT_STATUSES = {
    ReplyDraftStatus.GENERATED.value,
    ReplyDraftStatus.EDITED.value,
}
_QUEUEABLE_TASK_STATUSES = {
    ReplyTaskStatus.NEEDS_REPLY.value,
    ReplyTaskStatus.REPLIED.value,
}


class SqlReplySequenceWriter:
    """Atomically bind ordered drafts to one confirmed 60-second schedule."""

    def __init__(self, session: AsyncSession, *, now: datetime | None = None) -> None:
        self._session = session
        self._now = now

    async def queue(self, command: QueueApprovedReplySequenceCommand) -> QueuedReplySequence:
        validate_sequence_draft_ids(command.draft_ids)
        validate_sequence_payload_digests(command.draft_ids, command.expected_payload_sha256s)
        scope = command.principal.access_scope
        result = await self._session.execute(
            select(ReplyDraft, ReplyTask, Conversation, Message, Account)
            .options(undefer(ReplyTask.account_id))
            .join(ReplyTask, ReplyDraft.reply_task_id == ReplyTask.id)
            .join(Conversation, ReplyDraft.conversation_id == Conversation.id)
            .join(Message, ReplyDraft.trigger_message_id == Message.id)
            .join(Account, ReplyDraft.account_id == Account.id)
            .where(
                ReplyDraft.id.in_(command.draft_ids),
                ReplyDraft.account_id.in_(scope.account_ids),
                ReplyDraft.project_id.in_(scope.project_ids),
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
                Message.account_id.in_(scope.account_ids),
                Message.project_id.in_(scope.project_ids),
            )
            .with_for_update(of=(ReplyDraft, ReplyTask, Conversation, Message, Account))
        )
        rows = result.all()
        by_id = {
            draft.id: (draft, task, conversation, message, account)
            for draft, task, conversation, message, account in rows
        }
        if len(by_id) != len(command.draft_ids):
            raise ReplySequenceError(
                code="draft_not_found",
                message="One or more drafts are outside the allowed scope",
            )

        ordered = [by_id[draft_id] for draft_id in command.draft_ids]
        first_draft, first_task, first_conversation, first_message, account = ordered[0]
        binding = (
            first_draft.reply_task_id,
            first_draft.conversation_id,
            first_draft.account_id,
            first_draft.project_id,
            first_draft.trigger_message_id,
        )
        if any(
            (
                draft.reply_task_id,
                draft.conversation_id,
                draft.account_id,
                draft.project_id,
                draft.trigger_message_id,
            )
            != binding
            for draft, _task, _conversation, _message, _account in ordered
        ):
            raise ReplySequenceError(
                code="sequence_binding_mismatch",
                message="Every draft in a sequence must target the same reply task",
            )
        if first_task.status not in _QUEUEABLE_TASK_STATUSES:
            raise ReplySequenceError(
                code="task_closed",
                message="Reply task is not available for a confirmed sequence",
            )
        if any(draft.status not in _QUEUEABLE_DRAFT_STATUSES for draft, *_ in ordered):
            raise ReplySequenceError(
                code="draft_closed",
                message="Every sequence draft must be unsent and editable",
            )
        if any((draft.prompt_context or {}).get("kind") == "document" for draft, *_ in ordered):
            raise ReplySequenceError(
                code="document_confirmation_required",
                message="A document cannot be sent through the text sequence",
            )
        if any(SEQUENCE_CONTEXT_KEY in (draft.prompt_context or {}) for draft, *_ in ordered):
            raise ReplySequenceError(
                code="sequence_already_queued",
                message="One or more drafts already belong to a reply sequence",
            )

        try:
            for draft, _task, conversation, _message, _account in ordered:
                await require_saved_dialog_snapshot(
                    self._session, scope=scope, draft=draft, conversation=conversation,
                )
            if any((draft.prompt_context or {}).get(SNAPSHOT_KEY) !=
                   (first_draft.prompt_context or {}).get(SNAPSHOT_KEY)
                   for draft, *_ in ordered):
                raise ReplySendError(code="context_changed", message="Sequence snapshots differ")
        except ReplySendError as exc:
            raise ReplySequenceError(code=exc.code, message=exc.message) from exc

        attempts = await self._session.execute(
            select(SendAttempt.reply_draft_id).where(
                SendAttempt.reply_draft_id.in_(command.draft_ids)
            )
        )
        if attempts.scalars().first() is not None:
            raise ReplySequenceError(
                code="draft_already_attempted",
                message="A sequence cannot include a draft with a delivery attempt",
            )

        # The mandatory reviewed snapshot above decides freshness. A pending
        # sibling may predate that review; its existence is not a new event.
        # Dispatch rechecks the snapshot at the shared sender boundary.

        label = account_label(alias=account.alias, country_code=account.country_code)
        recipient_chat_id = first_message.sender_id
        if label != command.expected_account:
            raise ReplySequenceError(
                code="account_confirmation_mismatch",
                message="Confirmed account does not match the sequence account",
            )
        if recipient_chat_id != command.expected_recipient_chat_id:
            raise ReplySequenceError(
                code="recipient_confirmation_mismatch",
                message="Confirmed recipient does not match the sequence task",
            )

        payload_digests: list[str | None] = []
        for position, (draft, *_rest) in enumerate(ordered):
            try:
                formatting = stored_preview(draft.draft_text, draft.prompt_context or {})
            except FormattingError as exc:
                raise ReplySequenceError(code=exc.code, message=exc.message) from exc
            if formatting is not None and formatting.text_format == "rich":
                raise ReplySequenceError(
                    code="rich_surface_unsupported",
                    message="Rich reply sequences are unsupported in sprint 1",
                )
            if formatting is not None:
                if command.expected_payload_sha256s is None:
                    raise ReplySequenceError(
                        code="formatting_confirmation_required",
                        message="Confirm the exact ordered payload digests shown in the previews",
                    )
                if not hmac.compare_digest(
                    formatting.payload_sha256, command.expected_payload_sha256s[position]
                ):
                    raise ReplySequenceError(
                        code="formatting_confirmation_mismatch",
                        message="A sequence payload differs from the confirmed preview",
                    )
                payload_digests.append(formatting.payload_sha256)
            else:
                if command.expected_payload_sha256s is not None:
                    raise ReplySequenceError(
                        code="formatting_confirmation_mismatch",
                        message="A sequence draft has no matching formatting preview",
                    )
                payload_digests.append(None)

        sequence_id = uuid4()
        queued_at = self._now or datetime.now(UTC)
        items: list[QueuedReplySequenceItem] = []
        for position, (draft, *_rest) in enumerate(ordered):
            scheduled_at = queued_at + timedelta(seconds=position * command.interval_seconds)
            context = dict(draft.prompt_context or {})
            context[SEQUENCE_CONTEXT_KEY] = {
                "id": str(sequence_id),
                "position": position,
                "size": len(ordered),
                "interval_seconds": command.interval_seconds,
                "scheduled_at": scheduled_at.isoformat(),
                "state": "scheduled",
                "expected_account": label,
                "expected_recipient_chat_id": recipient_chat_id,
                "principal_subject": command.principal.subject,
                "workspace_id": scope.workspace_id,
                "payload_sha256": payload_digests[position],
            }
            draft.prompt_context = context
            items.append(
                QueuedReplySequenceItem(
                    draft_id=draft.id,
                    position=position,
                    scheduled_at=scheduled_at,
                    payload_sha256=payload_digests[position],
                )
            )

        self._session.add(
            AuditLog(
                actor=f"mcp:{command.principal.subject}"[:255],
                action="reply_sequence_queued_from_mcp",
                bot_id=first_conversation.bot_id,
                account_id=first_draft.account_id,
                project_id=first_draft.project_id,
                metadata_={
                    "sequence_id": str(sequence_id),
                    "task_id": str(first_task.id),
                    "draft_ids": [str(item.draft_id) for item in items],
                    "account_label": label,
                    "target_chat_id": recipient_chat_id,
                    "interval_seconds": command.interval_seconds,
                    "scheduled_at": [item.scheduled_at.isoformat() for item in items],
                },
            )
        )
        await self._session.flush()
        return QueuedReplySequence(
            sequence_id=sequence_id,
            task_id=first_task.id,
            account_id=first_draft.account_id,
            project_id=first_draft.project_id,
            account_alias=account.alias,
            account_label=label,
            recipient_chat_id=recipient_chat_id,
            interval_seconds=command.interval_seconds,
            items=tuple(items),
        )
