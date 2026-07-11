# mypy: ignore-errors
"""Sprint 15b Telegram diagnostics for account-scoped Business messages."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api import sprint14b_routes as sprint14b
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.scoped_admin import TelegramAdminContext, resolve_telegram_admin_context
from app.core.config import Settings, get_settings
from app.models.domain import (
    AuditLog,
    GateEvent,
    Message,
    MessageDirection,
    ReplyTask,
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
async def sprint15b_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle account-scoped Telegram diagnostics commands."""

    update = await sprint14._read_update(request)
    normalized = legacy._extract_update_event(update)
    if not _is_events_command(normalized.text if normalized else None):
        return await sprint14b.sprint14b_telegram_webhook(
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
    if existing_update is not None:
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": None,
            "duplicate": True,
            "send_status": "duplicate",
        }

    gate_event = GateEvent(
        bot_id=bot.id,
        chat_id=normalized.chat_id,
        sender_id=normalized.sender_id,
        event_type=normalized.event_type,
        text=normalized.text,
        payload=update,
        telegram_update_id=normalized.update_id,
        telegram_message_id=normalized.message_id,
        business_connection_id=normalized.business_connection_id,
    )
    session.add(
        TelegramUpdate(
            bot_id=bot.id,
            update_id=normalized.update_id,
            payload=update,
            processing_status="processed",
            processed_at=datetime.now(UTC),
        )
    )
    session.add(gate_event)
    await session.flush()

    admin_context = await resolve_telegram_admin_context(
        normalized=normalized,
        session=session,
    )
    if admin_context.is_admin:
        reply_text = await _build_recent_events_reply(
            session=session,
            admin_context=admin_context,
        )
        audit_action = "telegram_admin_console_events_sent"
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
                gate_event_id=gate_event.id,
                metadata_={
                    "command": normalized.text,
                    "error_type": exc.__class__.__name__,
                },
            )
        )
        await session.commit()
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": reply_text,
            "duplicate": False,
            "send_status": "failed",
        }

    session.add(
        Message(
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            direction=MessageDirection.OUTBOUND.value,
            source="gate",
            event_type="telegram_admin_console_reply",
            chat_id=normalized.chat_id,
            sender_id=settings.telegram_bot_username,
            recipient_id=normalized.chat_id,
            text=reply_text,
            business_connection_id=normalized.business_connection_id,
            payload=send_result,
            sent_at=datetime.now(UTC),
        )
    )
    session.add(
        AuditLog(
            actor="gate",
            action=audit_action,
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            metadata_={
                "command": normalized.text,
                "admin_context": admin_context.is_admin,
                "admin_super": admin_context.is_super_admin,
                "admin_account_alias": admin_context.account_alias,
                "admin_account_id": str(admin_context.account_id)
                if admin_context.account_id is not None
                else None,
            },
        )
    )
    await session.commit()

    return {
        "ok": True,
        "event_type": normalized.event_type,
        "auto_reply": reply_text,
        "duplicate": False,
        "send_status": "sent",
    }


async def _build_recent_events_reply(
    *,
    session: AsyncSession,
    admin_context: TelegramAdminContext,
) -> str:
    account_id = admin_context.scoped_account_id
    query = (
        select(Message, ReplyTask)
        .outerjoin(ReplyTask, ReplyTask.trigger_message_id == Message.id)
        .where(Message.direction == MessageDirection.INBOUND.value)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(10)
    )
    if account_id is not None:
        connection_ids = select(TelegramBusinessConnection.business_connection_id).where(
            TelegramBusinessConnection.account_id == account_id,
            TelegramBusinessConnection.is_enabled.is_(True),
        )
        query = query.where(
            or_(
                Message.account_id == account_id,
                Message.business_connection_id.in_(connection_ids),
            )
        )

    result = await session.execute(query)
    rows = result.all()
    scope = "all" if admin_context.is_super_admin else admin_context.account_alias or "unknown"
    lines = ["Recent inbound messages", f"scope: {scope}"]
    if not rows:
        lines.append("No recent inbound messages in this account scope.")
        return "\n".join(lines)

    for index, (message, task) in enumerate(rows, start=1):
        text = legacy._truncate_text(message.text or "", 140)
        task_label = "no"
        if task is not None:
            task_label = f"yes {str(task.id)[:8]} {task.status}"
        lines.extend(
            [
                "",
                f"{index}. {message.event_type}",
                f"chat: {message.chat_id}",
                f"sender: {message.sender_id}",
                f"account_id: {message.account_id or 'unknown'}",
                f"business_id: {_short_id(message.business_connection_id)}",
                f"task: {task_label}",
                f"created: {legacy._serialize_datetime(message.created_at)}",
                f"text: {text or '<empty>'}",
            ]
        )
    return "\n".join(lines)


def _is_events_command(text: str | None) -> bool:
    normalized = (text or "").strip().casefold()
    return normalized in {
        "/events",
        "events",
        "/messages",
        "messages",
        "события",
        "сообщения",
    }


def _short_id(value: str | None) -> str:
    if not value:
        return "unknown"
    if len(value) <= 14:
        return value
    return f"{value[:10]}...{value[-4:]}"
