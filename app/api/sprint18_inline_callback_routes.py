# mypy: ignore-errors
# ruff: noqa
"""Sprint 18 Telegram inline callback actions."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api import sprint17_send_draft_routes as sprint17
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.scoped_admin import resolve_telegram_admin_context
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, TelegramUpdate
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]


@router.post("/telegram/webhook")
async def sprint18_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle inline send callback before delegating other webhook updates."""

    update = await sprint14._read_update(request)
    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return await sprint17.sprint17_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    callback_data = str(callback.get("data") or "")
    draft_ref = _draft_ref_from_callback(callback_data)
    if draft_ref is None:
        return await sprint17.sprint17_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )
    if not isinstance(session, AsyncSession):
        return {
            "ok": True,
            "event_type": "callback_query",
            "callback_data": callback_data,
            "auto_reply": None,
            "duplicate": False,
            "send_status": "skipped_fake_session",
        }

    sprint14._require_telegram_secret(x_telegram_secret=x_telegram_secret)
    normalized = _normalize_callback_update(update=update, callback=callback)
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
        reply_text = await sprint17._send_draft_reply(
            session=session,
            telegram=telegram,
            settings=settings,
            draft_ref=draft_ref,
            account_id=admin_context.scoped_account_id,
            scope_label="all" if admin_context.is_super_admin else admin_context.account_alias or "unknown",
        )
        audit_action = "telegram_inline_send_draft_sent"
        answer_text = "Отправлено" if "status: sent" in reply_text else "Не отправлено"
    else:
        reply_text = "Gate online"
        audit_action = "telegram_inline_callback_rejected"
        answer_text = "Недоступно"

    callback_id = str(callback.get("id") or "")
    if callback_id:
        try:
            await telegram.answer_callback_query(callback_query_id=callback_id, text=answer_text)
        except Exception as exc:
            session.add(
                AuditLog(
                    actor="gate",
                    action="telegram_inline_callback_answer_failed",
                    bot_id=bot.id,
                    gate_event_id=None,
                    metadata_={"error_type": exc.__class__.__name__, "draft_ref": draft_ref},
                )
            )

    send_status = "not_sent"
    try:
        send_result = await telegram.send_message(
            chat_id=normalized.chat_id,
            text=reply_text,
            business_connection_id=normalized.business_connection_id,
        )
        send_status = "sent"
    except Exception as exc:
        send_result = None
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_inline_callback_reply_failed",
                bot_id=bot.id,
                gate_event_id=None,
                metadata_={"error_type": exc.__class__.__name__, "draft_ref": draft_ref},
            )
        )

    await _clear_callback_buttons(
        callback=callback,
        telegram=telegram,
        session=session,
        bot_id=bot.id,
        draft_ref=draft_ref,
    )
    session.add(
        AuditLog(
            actor="gate",
            action=audit_action,
            bot_id=bot.id,
            gate_event_id=None,
            metadata_={
                "draft_ref": draft_ref,
                "callback_data": callback_data,
                "admin_context": admin_context.is_admin,
                "admin_super": admin_context.is_super_admin,
                "admin_account_alias": admin_context.account_alias,
                "send_result": send_result,
                "reply_send_status": send_status,
            },
        )
    )
    await session.commit()
    return {
        "ok": True,
        "event_type": "callback_query",
        "callback_data": callback_data,
        "auto_reply": reply_text,
        "duplicate": existing_update is not None,
        "send_status": send_status,
    }


async def _clear_callback_buttons(
    *,
    callback: JsonObject,
    telegram: TelegramClient,
    session: AsyncSession,
    bot_id: Any,
    draft_ref: str,
) -> None:
    message = callback.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    message_id = message.get("message_id")
    if not isinstance(chat, dict) or message_id is None:
        return
    chat_id = chat.get("id")
    if chat_id is None:
        return
    business_connection_id = message.get("business_connection_id")
    try:
        await telegram.edit_message_reply_markup(
            chat_id=str(chat_id),
            message_id=int(message_id),
            business_connection_id=str(business_connection_id) if business_connection_id else None,
            reply_markup={"inline_keyboard": []},
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_inline_callback_clear_buttons_failed",
                bot_id=bot_id,
                gate_event_id=None,
                metadata_={"error_type": exc.__class__.__name__, "draft_ref": draft_ref},
            )
        )


def _normalize_callback_update(*, update: JsonObject, callback: JsonObject) -> legacy.TelegramUpdateEvent:
    sender = callback.get("from")
    message = callback.get("message")
    chat_id = ""
    message_id = None
    business_connection_id = callback.get("business_connection_id")
    if isinstance(message, dict):
        chat = message.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            chat_id = str(chat.get("id"))
        if message.get("message_id") is not None:
            message_id = legacy._optional_int(message.get("message_id"))
        business_connection_id = business_connection_id or message.get("business_connection_id")
    sender_id = ""
    if isinstance(sender, dict) and sender.get("id") is not None:
        sender_id = str(sender.get("id"))
    return legacy.TelegramUpdateEvent(
        update_id=int(update.get("update_id")),
        message_id=message_id,
        event_type="callback_query",
        chat_id=chat_id,
        sender_id=sender_id,
        text=str(callback.get("data") or ""),
        business_connection_id=str(business_connection_id) if business_connection_id else None,
    )


def _draft_ref_from_callback(callback_data: str) -> str | None:
    prefix = "gate:send:"
    if not callback_data.startswith(prefix):
        return None
    value = callback_data.removeprefix(prefix).strip()
    return value or None
