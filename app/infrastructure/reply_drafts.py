"""SQLAlchemy adapter for scoped reply-draft persistence."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.application.account_labels import account_label
from app.application.reply_drafts import (
    ReplyTaskClosedError,
    ReplyTaskNotFoundError,
    ReplyTaskSupersededError,
    SavedReplyDraft,
    SaveReplyDraftCommand,
)
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    Message,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
)


class SqlReplyDraftWriter:
    """Save drafts only for tasks inside the authenticated account/project grants."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, command: SaveReplyDraftCommand) -> SavedReplyDraft:
        scope = command.principal.access_scope
        locked_conversation_id = await self._session.scalar(
            select(Conversation.id)
            .join(ReplyTask, ReplyTask.conversation_id == Conversation.id)
            .join(Message, ReplyTask.trigger_message_id == Message.id)
            .join(Account, ReplyTask.account_id == Account.id)
            .where(
                ReplyTask.id == command.task_id,
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
                Message.account_id.in_(scope.account_ids),
                Message.project_id.in_(scope.project_ids),
            )
            .with_for_update(of=Conversation)
        )
        if locked_conversation_id is None:
            raise ReplyTaskNotFoundError()

        result = await self._session.execute(
            select(ReplyTask, Conversation, Message, Account)
            .options(undefer(ReplyTask.account_id))
            .join(Conversation, ReplyTask.conversation_id == Conversation.id)
            .join(Message, ReplyTask.trigger_message_id == Message.id)
            .join(Account, ReplyTask.account_id == Account.id)
            .where(
                ReplyTask.id == command.task_id,
                ReplyTask.conversation_id == locked_conversation_id,
                Conversation.id == locked_conversation_id,
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
                Message.account_id.in_(scope.account_ids),
                Message.project_id.in_(scope.project_ids),
            )
            .with_for_update(of=(ReplyTask, Message, Account))
        )
        row = result.one_or_none()
        if row is None:
            raise ReplyTaskNotFoundError()
        task, conversation, trigger_message, account = row
        is_follow_up = (
            task.status == ReplyTaskStatus.REPLIED.value and command.follow_up
        )
        if task.status != ReplyTaskStatus.NEEDS_REPLY.value and not is_follow_up:
            raise ReplyTaskClosedError()
        if is_follow_up:
            newer_task_id = await self._session.scalar(
                select(ReplyTask.id)
                .where(
                    ReplyTask.conversation_id == task.conversation_id,
                    ReplyTask.account_id == task.account_id,
                    ReplyTask.project_id == task.project_id,
                    ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
                    ReplyTask.id != task.id,
                )
                .order_by(ReplyTask.created_at.desc(), ReplyTask.id.desc())
                .limit(1)
            )
            if newer_task_id is not None:
                raise ReplyTaskSupersededError()

        label = account_label(alias=account.alias, country_code=account.country_code)
        recipient_chat_id = trigger_message.sender_id
        draft = ReplyDraft(
            account_id=task.account_id,
            project_id=task.project_id,
            person_id=task.person_id,
            context_binding_id=task.context_binding_id,
            reply_task_id=task.id,
            conversation_id=task.conversation_id,
            trigger_message_id=task.trigger_message_id,
            status=ReplyDraftStatus.GENERATED.value,
            source="chatgpt-mcp",
            draft_text=command.text,
            prompt_context={
                "bounded_context": True,
                "source": "chatgpt-mcp",
                "follow_up": is_follow_up,
            },
        )
        self._session.add(draft)
        await self._session.flush()
        self._session.add(
            AuditLog(
                actor=f"mcp:{command.principal.subject}"[:255],
                action=(
                    "reply_follow_up_draft_saved_from_mcp"
                    if is_follow_up
                    else "reply_draft_saved_from_mcp"
                ),
                account_id=task.account_id,
                project_id=task.project_id,
                metadata_={
                    "draft_id": str(draft.id),
                    "task_id": str(task.id),
                    "account_alias": account.alias,
                    "account_label": label,
                    "recipient_chat_id": recipient_chat_id,
                    "sent": False,
                },
            )
        )
        await self._session.flush()
        return SavedReplyDraft(
            draft_id=draft.id,
            task_id=task.id,
            account_id=task.account_id,
            project_id=task.project_id,
            account_alias=account.alias,
            account_label=label,
            recipient_chat_id=recipient_chat_id,
            status=draft.status,
            text=draft.draft_text,
        )
