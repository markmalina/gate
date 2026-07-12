"""SQLAlchemy adapter for scoped reply-draft persistence."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.application.reply_drafts import (
    ReplyTaskClosedError,
    ReplyTaskNotFoundError,
    SavedReplyDraft,
    SaveReplyDraftCommand,
)
from app.models.domain import (
    AuditLog,
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
        result = await self._session.execute(
            select(ReplyTask)
            .options(undefer(ReplyTask.account_id))
            .where(
                ReplyTask.id == command.task_id,
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
            )
            .with_for_update()
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise ReplyTaskNotFoundError()
        if task.status != ReplyTaskStatus.NEEDS_REPLY.value:
            raise ReplyTaskClosedError()

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
            prompt_context={"bounded_context": True, "source": "chatgpt-mcp"},
        )
        self._session.add(draft)
        await self._session.flush()
        self._session.add(
            AuditLog(
                actor=f"mcp:{command.principal.subject}"[:255],
                action="reply_draft_saved_from_mcp",
                account_id=task.account_id,
                project_id=task.project_id,
                metadata_={
                    "draft_id": str(draft.id),
                    "task_id": str(task.id),
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
            status=draft.status,
            text=draft.draft_text,
        )
