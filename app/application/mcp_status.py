# mypy: ignore-errors
"""Scoped application query for the Gate MCP status snapshot."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.account_labels import account_label
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    Message,
    MessageDirection,
    PersonIdentity,
    ReplyDraft,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
)

JsonObject = dict[str, Any]
ANSWERED_MESSAGE_SOURCES = ("telegram", "gate-approved-draft")


async def build_gate_status(
    *,
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    protocol_version: str,
    tool_names: list[str],
) -> JsonObject:
    """Build the status snapshot inside the caller's account and project scope."""

    accounts = principal.access_scope.account_ids
    projects = principal.access_scope.project_ids
    owner_telegram_ids = _owner_telegram_ids_subquery(accounts)
    counts = {
        "accounts": await _count(session=session, model=Account, where=(Account.id.in_(accounts),)),
        "business_connections": await _count(
            session=session,
            model=TelegramBusinessConnection,
            where=(TelegramBusinessConnection.account_id.in_(accounts),),
        ),
        "active_business_connections": await _count(
            session=session,
            model=TelegramBusinessConnection,
            where=(
                TelegramBusinessConnection.account_id.in_(accounts),
                TelegramBusinessConnection.is_enabled.is_(True),
            ),
        ),
        "conversations": await _count(
            session=session,
            model=Conversation,
            where=(Conversation.account_id.in_(accounts), Conversation.project_id.in_(projects)),
        ),
        "messages": await _count(
            session=session,
            model=Message,
            where=(Message.account_id.in_(accounts), Message.project_id.in_(projects)),
        ),
        "reply_tasks": await _count(
            session=session,
            model=ReplyTask,
            where=(ReplyTask.account_id.in_(accounts), ReplyTask.project_id.in_(projects)),
        ),
        "unanswered": await _count_unanswered(
            session=session,
            accounts=accounts,
            projects=projects,
        ),
        "answered_messages": await _count(
            session=session,
            model=Message,
            where=(
                Message.account_id.in_(accounts),
                Message.project_id.in_(projects),
                Message.direction == MessageDirection.OUTBOUND.value,
                Message.chat_id.not_in(select(owner_telegram_ids.c.owner_id)),
                _answered_message_filter(),
            ),
        ),
        "drafts": await _count(
            session=session,
            model=ReplyDraft,
            where=(ReplyDraft.account_id.in_(accounts), ReplyDraft.project_id.in_(projects)),
        ),
    }
    connection_result = await session.execute(
        select(Account, TelegramBusinessConnection)
        .outerjoin(
            TelegramBusinessConnection,
            TelegramBusinessConnection.account_id == Account.id,
        )
        .where(Account.id.in_(accounts))
        .order_by(
            Account.alias.asc(),
            TelegramBusinessConnection.is_enabled.desc(),
            TelegramBusinessConnection.last_update_at.desc(),
        )
    )
    latest_connections: dict[UUID, tuple[Account, TelegramBusinessConnection | None]] = {}
    for account, connection in connection_result.all():
        latest_connections.setdefault(account.id, (account, connection))
    connection_health = [
        serialize_connection_health(account=account, connection=connection)
        for account, connection in latest_connections.values()
    ]
    contact_directory = await _build_contact_directory_status(
        session=session,
        accounts=accounts,
    )
    return {
        "service": "gate",
        "environment": settings.gate_env,
        "commit": _commit(settings),
        "bot_username": settings.telegram_bot_username,
        "default_mode": settings.gate_default_mode,
        "send_enabled": settings.gate_send_enabled,
        "access": {
            "subject": principal.subject,
            "workspace_id": principal.access_scope.workspace_id,
            "account_count": len(accounts),
            "project_count": len(projects),
        },
        "counts": counts,
        "connection_health": connection_health,
        "contact_directory": contact_directory,
        "mcp": {
            "protocol_version": protocol_version,
            "tools": tool_names,
            "draft_writes_enabled": True,
            "send_enabled": settings.gate_send_enabled,
            "send_requires_explicit_confirmation": True,
            "auth": (
                "temporary_bearer_enforced"
                if principal.auth_method == "bearer"
                else f"{principal.auth_method}_enforced"
            ),
        },
    }


async def _build_contact_directory_status(
    *, session: AsyncSession, accounts: frozenset[UUID]
) -> list[JsonObject]:
    contact_counts = (
        select(
            PersonIdentity.account_id.label("account_id"),
            func.count(PersonIdentity.id).label("imported_contacts"),
        )
        .where(
            PersonIdentity.account_id.in_(accounts),
            PersonIdentity.platform == "telegram",
            PersonIdentity.metadata_["telegram_contact_name"].as_string().is_not(None),
        )
        .group_by(PersonIdentity.account_id)
        .subquery()
    )
    ranked_imports = (
        select(
            AuditLog.account_id.label("account_id"),
            AuditLog.created_at.label("created_at"),
            AuditLog.metadata_["source"].as_string().label("source"),
            func.row_number()
            .over(
                partition_by=AuditLog.account_id,
                order_by=[AuditLog.created_at.desc(), AuditLog.id.desc()],
            )
            .label("rank"),
        )
        .where(
            AuditLog.account_id.in_(accounts),
            AuditLog.action == "telegram_contact_snapshot_imported",
        )
        .subquery()
    )
    result = await session.execute(
        select(
            Account,
            func.coalesce(contact_counts.c.imported_contacts, 0),
            ranked_imports.c.created_at,
            ranked_imports.c.source,
        )
        .outerjoin(contact_counts, contact_counts.c.account_id == Account.id)
        .outerjoin(
            ranked_imports,
            and_(
                ranked_imports.c.account_id == Account.id,
                ranked_imports.c.rank == 1,
            ),
        )
        .where(Account.id.in_(accounts))
        .order_by(Account.alias.asc())
    )
    return [
        {
            "account": _serialize_account(account),
            "imported_contacts": int(imported_contacts),
            "last_source": source,
            "last_import_at": (
                _serialize_datetime(last_import_at) if last_import_at is not None else None
            ),
        }
        for account, imported_contacts, last_import_at, source in result.all()
    ]


async def _count_unanswered(
    *,
    session: AsyncSession,
    accounts: frozenset[UUID],
    projects: frozenset[UUID],
) -> int:
    result = await session.execute(
        select(func.count(func.distinct(ReplyTask.conversation_id)))
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
    )
    return int(result.scalar_one())


async def _count(
    *, session: AsyncSession, model: Any, where: tuple[Any, ...] = ()
) -> int:
    query = select(func.count(model.id)).where(*where)
    result = await session.execute(query)
    return int(result.scalar_one())


def _serialize_account(account: Account) -> JsonObject:
    return {
        "id": str(account.id),
        "alias": account.alias,
        "label": account_label(
            alias=account.alias,
            country_code=account.country_code,
        ),
    }


def serialize_connection_health(
    *,
    account: Account,
    connection: TelegramBusinessConnection | None,
) -> JsonObject:
    """Serialize connection readiness without exposing database objects."""

    if connection is None:
        state = "missing"
        can_reply = False
    else:
        can_reply = connection.rights.get("can_reply") is True
        if not connection.is_enabled:
            state = "inactive"
        elif not can_reply:
            state = "cannot_reply"
        elif connection.last_error:
            state = "degraded"
        else:
            state = "ready"
    return {
        "account": _serialize_account(account),
        "state": state,
        "connected": connection is not None and connection.is_enabled,
        "can_reply": can_reply,
        "last_checked_at": (
            _serialize_datetime(connection.last_update_at) if connection is not None else None
        ),
        "last_error": connection.last_error if connection is not None else None,
    }


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


def _serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _commit(settings: Settings) -> str:
    return settings.deployment_sha[:12] if settings.deployment_sha != "unknown" else "unknown"
