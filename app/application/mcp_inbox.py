# mypy: ignore-errors
"""Scoped application queries for the Gate MCP inbox and reply context."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, case, cast, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.application.account_labels import account_label
from app.core.mcp_security import AccessScope
from app.models.domain import (
    Account,
    Conversation,
    Message,
    MessageDirection,
    PersonIdentity,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
    TelegramUpdate,
)

JsonObject = dict[str, Any]
ANSWERED_MESSAGE_SOURCES = ("telegram", "gate-approved-draft")


async def build_unanswered_list(
    *, session: AsyncSession, scope: AccessScope, limit: int
) -> JsonObject:
    """Return the bounded inbox and recent answered messages inside one scope."""

    accounts = scope.account_ids
    projects = scope.project_ids
    owner_telegram_ids = _owner_telegram_ids_subquery(accounts)
    ranked_tasks = (
        select(
            ReplyTask.id.label("task_id"),
            func.row_number()
            .over(
                partition_by=ReplyTask.conversation_id,
                order_by=(ReplyTask.created_at.desc(), ReplyTask.id.desc()),
            )
            .label("conversation_rank"),
            func.count(ReplyTask.id)
            .over(partition_by=ReplyTask.conversation_id)
            .label("pending_message_count"),
        )
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
            ReplyTask.account_id.in_(accounts),
            ReplyTask.project_id.in_(projects),
            Conversation.account_id.in_(accounts),
            Conversation.project_id.in_(projects),
            Conversation.requires_reply.is_(True),
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.INBOUND.value,
            or_(
                Conversation.last_outbound_at.is_(None),
                func.coalesce(Message.received_at, Message.created_at)
                > Conversation.last_outbound_at,
            ),
            _non_service_message_filter(),
        )
        .subquery()
    )
    result = await session.execute(
        select(
            ReplyTask,
            Conversation,
            Message,
            TelegramUpdate,
            Account,
            ranked_tasks.c.pending_message_count,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .options(undefer(ReplyTask.account_id))
        .join(ranked_tasks, ranked_tasks.c.task_id == ReplyTask.id)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .join(Account, Message.account_id == Account.id)
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == Message.sender_id,
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(ranked_tasks.c.conversation_rank == 1)
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(limit)
    )
    items = [
        _serialize_unanswered_item(
            task=task,
            conversation=conversation,
            message=message,
            telegram_update=telegram_update,
            account=account,
            pending_message_count=pending_message_count,
            resolved_contact_name=resolved_contact_name,
        )
        for (
            task,
            conversation,
            message,
            telegram_update,
            account,
            pending_message_count,
            resolved_contact_name,
        ) in result.all()
    ]

    answered_task_id = (
        select(ReplyTask.id)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .where(
            ReplyTask.account_id == Message.account_id,
            ReplyTask.project_id == Message.project_id,
            Conversation.account_id == Message.account_id,
            Conversation.project_id == Message.project_id,
            Conversation.chat_id == Message.chat_id,
        )
        .order_by(ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(1)
        .correlate(Message)
        .scalar_subquery()
    )
    answered_result = await session.execute(
        select(
            Message,
            TelegramUpdate,
            Account,
            answered_task_id.label("reply_task_id"),
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .join(Account, Message.account_id == Account.id)
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id
                == func.coalesce(Message.recipient_id, Message.chat_id),
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            Message.account_id.in_(accounts),
            Message.project_id.in_(projects),
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.chat_id.not_in(select(owner_telegram_ids.c.owner_id)),
            _answered_message_filter(),
        )
        .order_by(
            func.coalesce(Message.sent_at, Message.created_at).desc(),
            Message.id.desc(),
        )
        .limit(limit)
    )
    answered_items = [
        _serialize_answered_item(
            message=message,
            telegram_update=telegram_update,
            account=account,
            task_id=task_id,
            resolved_contact_name=resolved_contact_name,
        )
        for (
            message,
            telegram_update,
            account,
            task_id,
            resolved_contact_name,
        ) in answered_result.all()
    ]
    return {
        "items": items,
        "count": len(items),
        "answered_items": answered_items,
        "answered_count": len(answered_items),
        "limit": limit,
        "next_cursor": None,
    }


async def get_reply_context(
    *,
    session: AsyncSession,
    scope: AccessScope,
    task_id: UUID,
    limit: int,
) -> JsonObject | None:
    """Return chronological reply context when the task belongs to the scope."""

    task_result = await session.execute(
        select(
            ReplyTask,
            Conversation,
            Message,
            TelegramUpdate,
            Account,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .options(undefer(ReplyTask.account_id))
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .join(Account, ReplyTask.account_id == Account.id)
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == Message.sender_id,
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            ReplyTask.id == task_id,
            ReplyTask.account_id.in_(scope.account_ids),
            ReplyTask.project_id.in_(scope.project_ids),
            Conversation.account_id.in_(scope.account_ids),
            Conversation.project_id.in_(scope.project_ids),
            Message.account_id.in_(scope.account_ids),
            Message.project_id.in_(scope.project_ids),
        )
    )
    row = task_result.one_or_none()
    if row is None:
        return None
    (
        task,
        conversation,
        trigger,
        trigger_update,
        account,
        resolved_contact_name,
    ) = row
    history_counterparty_user_id = case(
        (
            Message.direction == MessageDirection.INBOUND.value,
            Message.sender_id,
        ),
        else_=func.coalesce(Message.recipient_id, Message.chat_id),
    )
    history_result = await session.execute(
        select(
            Message,
            TelegramUpdate,
            PersonIdentity.metadata_["telegram_contact_name"]
            .as_string()
            .label("resolved_contact_name"),
        )
        .outerjoin(
            PersonIdentity,
            and_(
                PersonIdentity.account_id == Message.account_id,
                PersonIdentity.platform == "telegram",
                PersonIdentity.platform_user_id == history_counterparty_user_id,
            ),
        )
        .outerjoin(
            TelegramUpdate,
            and_(
                TelegramUpdate.bot_id == Message.bot_id,
                TelegramUpdate.update_id == Message.telegram_update_id,
            ),
        )
        .where(
            Message.account_id == conversation.account_id,
            Message.project_id == conversation.project_id,
            Message.chat_id == conversation.chat_id,
            Message.business_connection_id.is_not_distinct_from(
                conversation.business_connection_id
            ),
        )
        .order_by(
            func.coalesce(
                Message.sent_at,
                Message.received_at,
                Message.created_at,
            ).desc(),
            Message.id.desc(),
        )
        .limit(limit)
    )
    history_rows = list(reversed(history_result.all()))
    content = {
        "task_id": str(task.id),
        "task_ref": str(task.id)[:8],
        "status": task.status,
        "reply_required": task.status == ReplyTaskStatus.NEEDS_REPLY.value,
        "account": _serialize_account(account),
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
        "counterparty": counterparty(
            message=trigger,
            telegram_update=trigger_update,
            resolved_contact_name=resolved_contact_name,
            fallback_name=getattr(conversation, "title", None),
        ),
        "messages": [
            {
                **_serialize_message(message),
                "counterparty": counterparty(
                    message=message,
                    telegram_update=telegram_update,
                    resolved_contact_name=history_contact_name,
                    fallback_name=getattr(conversation, "title", None),
                ),
            }
            for message, telegram_update, history_contact_name in history_rows
        ],
        "count": len(history_rows),
        "limit": limit,
    }
    return content


def _serialize_unanswered_item(
    *,
    task: ReplyTask,
    conversation: Conversation,
    message: Message,
    telegram_update: TelegramUpdate | None,
    account: Account,
    pending_message_count: int,
    resolved_contact_name: str | None,
) -> JsonObject:
    task_id = str(task.id)
    return {
        "task_id": task_id,
        "task_ref": task_id[:8],
        "status": task.status,
        "priority": task.priority,
        "reason": task.reason,
        "pending_message_count": int(pending_message_count),
        "account_id": str(task.account_id),
        "project_id": str(task.project_id),
        "account": _serialize_account(account),
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
        "counterparty": counterparty(
            message=message,
            telegram_update=telegram_update,
            resolved_contact_name=resolved_contact_name,
            fallback_name=getattr(conversation, "title", None),
        ),
        "message": _serialize_message(message),
        "created_at": _serialize_datetime(task.created_at),
    }


def _serialize_answered_item(
    *,
    message: Message,
    telegram_update: TelegramUpdate | None,
    account: Account,
    task_id: UUID | None,
    resolved_contact_name: str | None,
) -> JsonObject:
    serialized_task_id = str(task_id) if task_id is not None else None
    return {
        "task_id": serialized_task_id,
        "task_ref": serialized_task_id[:8] if serialized_task_id else None,
        "follow_up_available": task_id is not None,
        "account_id": str(message.account_id),
        "project_id": str(message.project_id),
        "account": _serialize_account(account),
        "counterparty": counterparty(
            message=message,
            telegram_update=telegram_update,
            resolved_contact_name=resolved_contact_name,
        ),
        "message": _serialize_message(message),
    }


def _serialize_account(account: Account) -> JsonObject:
    return {
        "id": str(account.id),
        "alias": account.alias,
        "label": account_label(
            alias=account.alias,
            country_code=account.country_code,
        ),
    }


def _serialize_message(message: Message) -> JsonObject:
    return {
        "id": str(message.id),
        "direction": message.direction,
        "source": message.source,
        "chat_id": message.chat_id,
        "sender_id": message.sender_id,
        "recipient_id": message.recipient_id,
        "text_preview": _truncate_text(message.text or "", 240),
        "created_at": _serialize_datetime(message.created_at),
        "received_at": _serialize_datetime(message.received_at),
        "sent_at": _serialize_datetime(message.sent_at),
    }


def counterparty(
    *,
    message: Message,
    telegram_update: TelegramUpdate | None,
    resolved_contact_name: str | None = None,
    fallback_name: str | None = None,
) -> JsonObject:
    payload = telegram_update.payload if telegram_update is not None else {}
    telegram_message = _telegram_message_payload(payload)
    raw_person = (
        telegram_message.get("from")
        if message.direction == MessageDirection.INBOUND.value
        else telegram_message.get("chat")
    )
    profile = raw_person if isinstance(raw_person, dict) else {}
    first_name = _optional_text(profile.get("first_name"))
    last_name = _optional_text(profile.get("last_name"))
    title = _optional_text(profile.get("title"))
    username = _optional_text(profile.get("username"))
    profile_name = " ".join(
        part for part in (first_name, last_name) if part
    ) or title
    snapshot = _counterparty_snapshot(message)
    snapshot_name = _optional_text(snapshot.get("display_name"))
    imported_name = _optional_text(resolved_contact_name)
    display_name = imported_name or snapshot_name or profile_name or fallback_name
    platform_user_id = _optional_text(profile.get("id")) or (
        message.sender_id
        if message.direction == MessageDirection.INBOUND.value
        else message.recipient_id or message.chat_id
    )
    label = display_name or (f"@{username}" if username else None) or platform_user_id
    return {
        "platform": "telegram",
        "platform_user_id": platform_user_id,
        "display_name": display_name,
        "username": username,
        "label": label,
    }


def _counterparty_snapshot(message: Message) -> JsonObject:
    payload = getattr(message, "payload", {})
    if not isinstance(payload, dict):
        return {}
    value = payload.get("_gate_counterparty")
    return value if isinstance(value, dict) else {}

def _telegram_message_payload(payload: Any) -> JsonObject:
    if not isinstance(payload, dict):
        return {}
    for key in ("business_message", "edited_business_message", "message"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _owner_telegram_ids_subquery(accounts: frozenset[UUID]) -> Any:
    owner_names = select(Account.owner).where(Account.id.in_(accounts))
    return union_all(
        select(cast(Account.telegram_user_id, String).label("owner_id")).where(
            Account.owner.in_(owner_names),
            Account.telegram_user_id.is_not(None),
        ),
        select(
            cast(TelegramBusinessConnection.telegram_user_id, String).label("owner_id")
        )
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .where(
            Account.owner.in_(owner_names),
            TelegramBusinessConnection.telegram_user_id.is_not(None),
        ),
        select(
            cast(TelegramBusinessConnection.user_chat_id, String).label("owner_id")
        )
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .where(
            Account.owner.in_(owner_names),
            TelegramBusinessConnection.user_chat_id.is_not(None),
        ),
    ).subquery()


def _answered_message_filter() -> Any:
    return and_(
        Message.source.in_(ANSWERED_MESSAGE_SOURCES),
        Message.text.is_not(None),
        func.length(func.trim(Message.text)) > 0,
        _non_service_message_filter(),
    )


def _non_service_message_filter() -> Any:
    return or_(
        Message.text.is_(None),
        and_(
            Message.text.notilike("/start bizChat%"),
            Message.text.notilike("/webhook%"),
            Message.text.notilike("/events%"),
            Message.text.notilike("/messages%"),
            Message.text.notilike("/raw%"),
            Message.text.notilike("/status%"),
            Message.text.notilike("/inbox%"),
            Message.text.notilike("/draft%"),
            Message.text.notilike("/drafts%"),
            Message.text.notilike("/done%"),
            Message.text.notilike("/dismiss%"),
            Message.text.notilike("/snooze%"),
            Message.text.notilike("/reopen%"),
            Message.text.notilike("/business%"),
            Message.text.notilike("Commit:%"),
            Message.text.notilike("TEST-%"),
        ),
    )


def _truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


