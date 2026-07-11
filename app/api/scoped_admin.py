# mypy: ignore-errors
"""Account-scoped Telegram admin helpers for Gate."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api import routes as legacy
from app.core.config import Settings
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    ConversationStatus,
    Message,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TelegramAdminContext:
    """Resolved Telegram admin scope for a command."""

    is_admin: bool
    is_super_admin: bool = False
    account_id: Any | None = None
    account_alias: str | None = None

    @property
    def scoped_account_id(self) -> Any | None:
        return None if self.is_super_admin else self.account_id


async def resolve_telegram_admin_context(
    *,
    normalized: legacy.TelegramUpdateEvent,
    session: AsyncSession,
) -> TelegramAdminContext:
    """Resolve whether the Telegram sender is a super-admin or account admin."""

    if legacy._is_telegram_admin_sender(normalized.sender_id):
        return TelegramAdminContext(
            is_admin=True,
            is_super_admin=True,
            account_alias="super_admin",
        )

    sender_id = legacy._optional_int(normalized.sender_id)
    if sender_id is None:
        return TelegramAdminContext(is_admin=False)

    result = await session.execute(
        select(TelegramBusinessConnection, Account)
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .where(
            TelegramBusinessConnection.user_chat_id == sender_id,
            TelegramBusinessConnection.is_enabled.is_(True),
        )
        .order_by(TelegramBusinessConnection.last_update_at.desc())
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return TelegramAdminContext(is_admin=False)

    connection, account = row
    return TelegramAdminContext(
        is_admin=True,
        is_super_admin=False,
        account_id=connection.account_id,
        account_alias=account.alias,
    )


async def count_scoped_unanswered_reply_tasks(
    *,
    session: AsyncSession,
    account_id: Any | None,
) -> int:
    result = await session.execute(
        _scoped_reply_task_query(account_id=account_id).where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value
        )
    )
    return len(result.all())


async def build_scoped_telegram_admin_reply(
    *,
    command: legacy.TelegramAdminCommand,
    admin_context: TelegramAdminContext,
    session: AsyncSession,
    settings: Settings,
) -> str:
    account_id = admin_context.scoped_account_id
    if command.name == "inbox":
        reply_tasks = await _load_scoped_unanswered_reply_tasks(
            session=session,
            limit=10,
            account_id=account_id,
        )
        return _format_scoped_inbox(
            reply_tasks=reply_tasks,
            admin_context=admin_context,
        )

    if command.name == "test":
        return legacy._format_telegram_test_guide(
            commit_sha=settings.deployment_sha,
            bot_username=settings.telegram_bot_username,
        )

    if command.name == "drafts":
        return await _list_scoped_reply_drafts(
            command=command,
            session=session,
            account_id=account_id,
        )

    if command.name == "draft":
        return await _create_scoped_reply_draft(
            command=command,
            session=session,
            account_id=account_id,
        )

    return await _apply_scoped_reply_task_action(
        command=command,
        session=session,
        account_id=account_id,
    )


async def _load_scoped_unanswered_reply_tasks(
    *,
    session: AsyncSession,
    limit: int,
    account_id: Any | None,
) -> list[JsonObject]:
    result = await session.execute(
        _scoped_reply_task_query(account_id=account_id)
        .where(ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value)
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(limit)
    )
    return [
        legacy._serialize_reply_task(task, conversation, message)
        for task, conversation, message in result.all()
    ]


async def _create_scoped_reply_draft(
    *,
    command: legacy.TelegramAdminCommand,
    session: AsyncSession,
    account_id: Any | None,
) -> str:
    rows_or_error = await _load_scoped_single_reply_task_row(
        command=command,
        session=session,
        account_id=account_id,
    )
    if isinstance(rows_or_error, str):
        return rows_or_error

    task, conversation, message = rows_or_error
    draft_text = legacy._build_reply_draft_text(message.text)
    prompt_context: JsonObject = {
        "bounded_context": True,
        "source_message": {
            "id": str(message.id),
            "chat_id": message.chat_id,
            "text": legacy._truncate_text(message.text or "", 600),
            "created_at": legacy._serialize_datetime(message.created_at),
            "received_at": legacy._serialize_datetime(message.received_at),
        },
        "reply_task": {
            "id": str(task.id),
            "status": task.status,
            "reason": task.reason,
        },
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
            "account_id": str(conversation.account_id)
            if conversation.account_id is not None
            else None,
        },
    }
    draft = ReplyDraft(
        reply_task_id=task.id,
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        status=ReplyDraftStatus.GENERATED.value,
        source=legacy.DRAFT_SOURCE,
        draft_text=draft_text,
        prompt_context=prompt_context,
    )
    session.add(draft)
    await session.flush()
    session.add(
        AuditLog(
            actor="gate",
            action="reply_draft_created_from_telegram",
            bot_id=conversation.bot_id,
            gate_event_id=None,
            account_id=conversation.account_id,
            project_id=conversation.project_id,
            metadata_={
                "task_id": str(task.id),
                "draft_id": str(draft.id),
                "source": legacy.DRAFT_SOURCE,
                "account_scoped": account_id is not None,
            },
        )
    )
    return legacy._format_reply_draft_result(draft=draft, task=task)


async def _list_scoped_reply_drafts(
    *,
    command: legacy.TelegramAdminCommand,
    session: AsyncSession,
    account_id: Any | None,
) -> str:
    rows_or_error = await _load_scoped_single_reply_task_row(
        command=command,
        session=session,
        account_id=account_id,
    )
    if isinstance(rows_or_error, str):
        return rows_or_error

    task, _conversation, _message = rows_or_error
    result = await session.execute(
        select(ReplyDraft)
        .where(ReplyDraft.reply_task_id == task.id)
        .order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .limit(5)
    )
    drafts = list(result.scalars().all())
    return legacy._format_reply_drafts_result(task=task, drafts=drafts)


async def _apply_scoped_reply_task_action(
    *,
    command: legacy.TelegramAdminCommand,
    session: AsyncSession,
    account_id: Any | None,
) -> str:
    rows_or_error = await _load_scoped_single_reply_task_row(
        command=command,
        session=session,
        account_id=account_id,
    )
    if isinstance(rows_or_error, str):
        return rows_or_error

    task, conversation, _message = rows_or_error
    old_status = task.status
    action_statuses = {
        "done": ReplyTaskStatus.REPLIED.value,
        "dismiss": ReplyTaskStatus.DISMISSED.value,
        "snooze": ReplyTaskStatus.SNOOZED.value,
        "reopen": ReplyTaskStatus.NEEDS_REPLY.value,
    }
    task.status = action_statuses[command.name]
    task.due_at = None

    if command.name == "reopen":
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
    elif command.name == "done":
        conversation.status = ConversationStatus.REPLIED.value
        conversation.requires_reply = False
    elif command.name == "dismiss":
        conversation.status = ConversationStatus.CLOSED.value
        conversation.requires_reply = False
    else:
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = False

    session.add(
        AuditLog(
            actor="gate",
            action=f"reply_task_{command.name}_from_telegram",
            bot_id=conversation.bot_id,
            gate_event_id=None,
            account_id=conversation.account_id,
            project_id=conversation.project_id,
            metadata_={
                "task_id": str(task.id),
                "task_ref": command.task_ref,
                "from_status": old_status,
                "to_status": task.status,
                "account_scoped": account_id is not None,
            },
        )
    )
    return legacy._format_reply_task_action_result(command=command, task=task)


async def _load_scoped_single_reply_task_row(
    *,
    command: legacy.TelegramAdminCommand,
    session: AsyncSession,
    account_id: Any | None,
) -> tuple[ReplyTask, Conversation, Message] | str:
    task_ref = (command.task_ref or "").strip()
    if not task_ref:
        return f"Usage: /{command.name} <task>"
    if len(task_ref) < 4:
        return "Task reference is too short. Use the task id from /inbox."

    rows = await _find_scoped_reply_task_rows_by_ref(
        session=session,
        task_ref=task_ref,
        account_id=account_id,
    )
    if not rows:
        return f"Task not found in this account scope: {task_ref}"
    if len(rows) > 1:
        return f"Task reference is ambiguous in this account scope: {task_ref}"
    return rows[0]


async def _find_scoped_reply_task_rows_by_ref(
    *,
    session: AsyncSession,
    task_ref: str,
    account_id: Any | None,
) -> list[tuple[ReplyTask, Conversation, Message]]:
    result = await session.execute(
        _scoped_reply_task_query(account_id=account_id)
        .where(sql_cast(ReplyTask.id, String).like(f"{task_ref}%"))
        .order_by(ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(2)
    )
    return [(task, conversation, message) for task, conversation, message in result.all()]


def _scoped_reply_task_query(*, account_id: Any | None) -> Any:
    query = (
        select(ReplyTask, Conversation, Message)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
    )
    if account_id is not None:
        query = query.where(ReplyTask.account_id == account_id)
    return query


def _format_scoped_inbox(
    *,
    reply_tasks: list[JsonObject],
    admin_context: TelegramAdminContext,
) -> str:
    text = legacy._format_telegram_unanswered_inbox(reply_tasks)
    if admin_context.is_super_admin:
        return "\n".join(["scope: all", "", text])
    return "\n".join([f"scope: {admin_context.account_alias or 'unknown'}", "", text])
