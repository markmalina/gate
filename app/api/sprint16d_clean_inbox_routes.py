# mypy: ignore-errors
# ruff: noqa
"""Sprint 16d clean Telegram inbox diagnostics."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import String, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api import sprint16_raw_routes as sprint16_raw
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.scoped_admin import resolve_telegram_admin_context
from app.core.config import Settings, get_settings
from app.models.domain import (
    AuditLog,
    Conversation,
    Message,
    MessageDirection,
    ReplyTask,
    ReplyTaskStatus,
    TelegramBusinessConnection,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]


@router.post("/telegram/webhook")
async def sprint16d_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle clean scoped /inbox before delegating other webhook updates."""

    update = await sprint14._read_update(request)
    normalized = legacy._extract_update_event(update)
    if normalized is None or not _is_inbox_command(normalized.text):
        return await sprint16_raw.sprint16_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    sprint14._require_telegram_secret(x_telegram_secret=x_telegram_secret)
    bot = await legacy._get_or_create_bot(
        session=session,
        username=settings.telegram_bot_username,
    )
    existing_update = await legacy._find_telegram_update(
        session=session,
        bot_id=bot.id,
        update_id=normalized.update_id,
    )
    if existing_update is None:
        session.add(
            TelegramUpdate(
                bot_id=bot.id,
                update_id=normalized.update_id,
                payload=update,
                processing_status="processed",
                processed_at=datetime.now(UTC),
            )
        )
        await session.flush()

    admin_context = await resolve_telegram_admin_context(
        normalized=normalized,
        session=session,
    )
    if admin_context.is_admin:
        reply_text = await _build_clean_inbox_reply(
            session=session,
            settings=settings,
            account_id=admin_context.scoped_account_id,
            scope_label="all" if admin_context.is_super_admin else admin_context.account_alias or "unknown",
        )
        audit_action = "telegram_admin_console_clean_inbox_sent"
    else:
        reply_text = "Gate online"
        audit_action = "telegram_admin_console_rejected"

    try:
        send_result = await telegram.send_message(
            chat_id=normalized.chat_id,
            text=reply_text,
            business_connection_id=normalized.business_connection_id,
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_admin_console_reply_failed",
                bot_id=bot.id,
                gate_event_id=None,
                metadata_={"command": normalized.text, "error_type": exc.__class__.__name__},
            )
        )
        await session.commit()
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": reply_text,
            "duplicate": existing_update is not None,
            "send_status": "failed",
        }

    session.add(
        AuditLog(
            actor="gate",
            action=audit_action,
            bot_id=bot.id,
            gate_event_id=None,
            metadata_={
                "command": normalized.text,
                "admin_context": admin_context.is_admin,
                "admin_super": admin_context.is_super_admin,
                "admin_account_alias": admin_context.account_alias,
                "send_result": send_result,
            },
        )
    )
    await session.commit()
    return {
        "ok": True,
        "event_type": normalized.event_type,
        "auto_reply": reply_text,
        "duplicate": existing_update is not None,
        "send_status": "sent",
    }


async def _build_clean_inbox_reply(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: Any | None,
    scope_label: str,
) -> str:
    query = (
        select(ReplyTask, Conversation, Message)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value)
        .where(Message.direction == MessageDirection.INBOUND.value)
        .where(_non_service_message_filter())
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(10)
    )
    if account_id is not None:
        connection_ids = select(TelegramBusinessConnection.business_connection_id).where(
            TelegramBusinessConnection.account_id == account_id,
            TelegramBusinessConnection.is_enabled.is_(True),
        )
        user_chat_ids = select(sql_cast(TelegramBusinessConnection.user_chat_id, String)).where(
            TelegramBusinessConnection.account_id == account_id,
            TelegramBusinessConnection.is_enabled.is_(True),
            TelegramBusinessConnection.user_chat_id.is_not(None),
        )
        query = query.where(
            or_(
                Conversation.account_id == account_id,
                Conversation.business_connection_id.in_(connection_ids),
                Message.chat_id.in_(user_chat_ids),
                Message.sender_id.in_(user_chat_ids),
            )
        )

    result = await session.execute(query)
    rows = result.all()
    reply_tasks = [
        legacy._serialize_reply_task(task, conversation, message)
        for task, conversation, message in rows
    ]
    body = legacy._format_telegram_unanswered_inbox(reply_tasks)
    return "\n".join([_commit_header(settings), "", f"scope: {scope_label}", "", body])


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
        ),
    )


def _is_inbox_command(text: str | None) -> bool:
    normalized = (text or "").strip().casefold()
    return normalized in {"/inbox", "inbox", "инбокс", "входящие"}


def _commit_header(settings: Settings) -> str:
    return f"Commit: {(settings.deployment_sha or 'unknown')[:12]}"
