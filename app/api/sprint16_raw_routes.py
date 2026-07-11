# mypy: ignore-errors
# ruff: noqa
"""Sprint 16 Telegram raw update diagnostics."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api import sprint15b_routes as sprint15b
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.scoped_admin import resolve_telegram_admin_context
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, Message, TelegramUpdate
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]


@router.post("/telegram/webhook")
async def sprint16_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Expose raw Telegram update diagnostics while delegating normal traffic."""

    update = await sprint14._read_update(request)
    normalized = legacy._extract_update_event(update)

    if normalized is None:
        if isinstance(session, AsyncSession):
            return await _store_unsupported_raw_update(
                update=update,
                x_telegram_secret=x_telegram_secret,
                settings=settings,
                session=session,
            )
        return await sprint15b.sprint15b_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    if not _is_raw_updates_command(normalized.text):
        return await sprint15b.sprint15b_telegram_webhook(
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
        reply_text = await _build_raw_updates_reply(
            session=session,
            settings=settings,
            current_update_id=normalized.update_id,
        )
        audit_action = "telegram_admin_console_raw_updates_sent"
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
                "admin_account_id": str(admin_context.account_id)
                if admin_context.account_id is not None
                else None,
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


async def _store_unsupported_raw_update(
    *,
    update: JsonObject,
    x_telegram_secret: str | None,
    settings: Settings,
    session: AsyncSession,
) -> JsonObject:
    sprint14._require_telegram_secret(x_telegram_secret=x_telegram_secret)
    update_id = update.get("update_id")
    update_types = _payload_update_types(update)
    if type(update_id) is not int:
        return {
            "ok": True,
            "ignored": True,
            "reason": "unsupported_update_without_update_id",
            "update_types": update_types,
        }

    bot = await legacy._get_or_create_bot(
        session=session,
        username=settings.telegram_bot_username,
    )
    existing_update = await legacy._find_telegram_update(
        session=session,
        bot_id=bot.id,
        update_id=update_id,
    )
    if existing_update is None:
        session.add(
            TelegramUpdate(
                bot_id=bot.id,
                update_id=update_id,
                payload=update,
                processing_status="ignored",
                error="Unsupported Telegram update",
                processed_at=datetime.now(UTC),
            )
        )
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_update_ignored_raw",
                bot_id=bot.id,
                gate_event_id=None,
                metadata_={
                    "telegram_update_id": update_id,
                    "update_types": update_types,
                },
            )
        )
        await session.commit()
    return {
        "ok": True,
        "ignored": True,
        "reason": "unsupported_update",
        "update_id": update_id,
        "update_types": update_types,
        "duplicate": existing_update is not None,
    }


async def _build_raw_updates_reply(
    *,
    session: AsyncSession,
    settings: Settings,
    current_update_id: int,
) -> str:
    result = await session.execute(
        select(TelegramUpdate)
        .where(TelegramUpdate.update_id != current_update_id)
        .order_by(TelegramUpdate.received_at.desc(), TelegramUpdate.id.desc())
        .limit(10)
    )
    updates = list(result.scalars().all())
    lines = [_commit_header(settings), "", "Recent raw Telegram updates"]
    if not updates:
        lines.append("No raw Telegram updates stored yet.")
        return "\n".join(lines)

    for index, raw_update in enumerate(updates, start=1):
        payload = raw_update.payload or {}
        meta = _raw_payload_metadata(payload)
        lines.extend(
            [
                "",
                f"{index}. update_id: {raw_update.update_id}",
                f"types: {', '.join(_payload_update_types(payload)) or 'unknown'}",
                f"status: {raw_update.processing_status}",
                f"error: {raw_update.error or 'none'}",
                f"business_id: {_short_id(meta.get('business_connection_id'))}",
                f"chat: {meta.get('chat_id') or 'unknown'}",
                f"sender: {meta.get('sender_id') or 'unknown'}",
                f"received: {legacy._serialize_datetime(raw_update.received_at)}",
                f"processed: {legacy._serialize_datetime(raw_update.processed_at)}",
                f"text: {legacy._truncate_text(meta.get('text') or '', 160) or '<empty>'}",
            ]
        )
    return "\n".join(lines)


def _raw_payload_metadata(payload: JsonObject) -> dict[str, str | None]:
    message = _raw_payload_message(payload)
    business_connection = payload.get("business_connection")
    deleted_messages = payload.get("deleted_business_messages")
    business_connection_id = None
    chat_id = None
    sender_id = None
    text = None

    if isinstance(message, dict):
        business_connection_id = message.get("business_connection_id")
        chat = message.get("chat")
        sender = message.get("from")
        chat_id = str(chat.get("id")) if isinstance(chat, dict) and chat.get("id") is not None else None
        sender_id = str(sender.get("id")) if isinstance(sender, dict) and sender.get("id") is not None else None
        text = message.get("text") or message.get("caption")
    if isinstance(business_connection, dict):
        business_connection_id = business_connection_id or business_connection.get("id")
        user_chat_id = business_connection.get("user_chat_id")
        if user_chat_id is not None:
            chat_id = chat_id or str(user_chat_id)
            sender_id = sender_id or str(user_chat_id)
    if isinstance(deleted_messages, dict):
        business_connection_id = business_connection_id or deleted_messages.get("business_connection_id")
        chat = deleted_messages.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            chat_id = chat_id or str(chat.get("id"))
    return {
        "business_connection_id": str(business_connection_id) if business_connection_id else None,
        "chat_id": chat_id,
        "sender_id": sender_id,
        "text": str(text) if text else None,
    }


def _raw_payload_message(payload: JsonObject) -> JsonObject | None:
    for key in ("business_message", "message", "edited_business_message"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return None


def _payload_update_types(payload: JsonObject) -> list[str]:
    return sorted(str(key) for key in payload if key != "update_id")


def _commit_header(settings: Settings) -> str:
    return f"Commit: {(settings.deployment_sha or 'unknown')[:12]}"


def _is_raw_updates_command(text: str | None) -> bool:
    normalized = (text or "").strip().casefold()
    return normalized in {
        "/raw_updates",
        "raw_updates",
        "/raw",
        "raw",
        "/сырые",
        "сырые",
    }


def _short_id(value: str | None) -> str:
    if not value:
        return "unknown"
    if len(value) <= 14:
        return value
    return f"{value[:10]}...{value[-4:]}"
