"""Bind sequence approval to projected, visible inbound context, not a fresh counter alone."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mcp_security import AccessScope
from app.models.domain import Conversation, Message, ReplyTask, TelegramReceipt
from app.models.reply_sequences import ReplyDialogGuard, ReplyInboundSignal


@dataclass(frozen=True, slots=True)
class ApprovalContextState:
    conversation_id: UUID
    trigger_message_id: UUID
    guard_id: UUID | None
    revision: int
    health: str
    pending: bool
    latest_message_id: UUID | None

    @property
    def blocked_reason(self) -> str | None:
        if self.health != "healthy":
            return "sequence_guard_unavailable"
        if self.pending or (self.revision and self.latest_message_id is None):
            return "recipient_context_pending"
        if self.revision and self.latest_message_id != self.trigger_message_id:
            return "recipient_context_changed"
        return None


async def approval_context_state(
    session: AsyncSession, scope: AccessScope, task_id: UUID
) -> ApprovalContextState | None:
    # Select scalar columns: an ORM identity map must not hide a concurrent revision.
    row = (
        await session.execute(
            select(
                Conversation.id,
                Conversation.account_id,
                Conversation.project_id,
                Conversation.chat_id,
                Conversation.business_connection_id,
                ReplyTask.trigger_message_id,
                ReplyDialogGuard.id,
                ReplyDialogGuard.inbound_revision,
                ReplyDialogGuard.health_state,
                ReplyInboundSignal.provider_message_id,
            )
            .select_from(ReplyTask)
            .join(
                Conversation,
                and_(
                    Conversation.id == ReplyTask.conversation_id,
                    Conversation.account_id == ReplyTask.account_id,
                    Conversation.project_id == ReplyTask.project_id,
                ),
            )
            .outerjoin(
                ReplyDialogGuard,
                and_(
                    ReplyDialogGuard.workspace_ref == scope.workspace_id,
                    ReplyDialogGuard.account_id == Conversation.account_id,
                    ReplyDialogGuard.provider_chat_id == Conversation.chat_id,
                ),
            )
            .outerjoin(
                ReplyInboundSignal,
                and_(
                    ReplyInboundSignal.dialog_guard_id == ReplyDialogGuard.id,
                    ReplyInboundSignal.receipt_ref == ReplyDialogGuard.last_inbound_receipt_ref,
                    ReplyInboundSignal.revision == ReplyDialogGuard.inbound_revision,
                ),
            )
            .where(
                ReplyTask.id == task_id,
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    (
        conversation,
        account,
        project,
        chat,
        connection,
        trigger,
        guard,
        revision,
        health,
        provider,
    ) = row
    pending = False
    if guard is not None:
        # Check every receipt: consumers may complete later revisions out of order.
        pending = bool(
            await session.scalar(
                select(
                    select(ReplyInboundSignal.id)
                    .join(TelegramReceipt, TelegramReceipt.id == ReplyInboundSignal.receipt_ref)
                    .where(
                        ReplyInboundSignal.dialog_guard_id == guard,
                        TelegramReceipt.status != "processed",
                    )
                    .exists()
                )
            )
        )
    latest = None
    if provider is not None and provider.isdecimal():
        matches = (
            await session.scalars(
                select(Message.id)
                .where(
                    Message.account_id == account,
                    Message.project_id == project,
                    Message.chat_id == chat,
                    Message.business_connection_id.is_not_distinct_from(connection),
                    Message.telegram_message_id == int(provider),
                    Message.direction == "inbound",
                )
                .limit(2)
            )
        ).all()
        if len(matches) == 1:
            latest = matches[0]
    return ApprovalContextState(
        conversation, trigger, guard, revision or 0, health or "healthy", pending, latest
    )


def context_approval_reason(
    before: ApprovalContextState | None,
    after: ApprovalContextState | None,
    visible_message_ids: set[str],
) -> str | None:
    if before is None or after is None or before != after:
        return "recipient_context_changed"
    if before.blocked_reason:
        return before.blocked_reason
    if str(before.trigger_message_id) not in visible_message_ids:
        return "recipient_context_not_visible"
    return None
