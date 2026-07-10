# mypy: ignore-errors
"""Sprint 11b Telegram inline actions for draft editing and sending."""

from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint9_routes as sprint9
from app.api import sprint10_routes as sprint10
from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(legacy.get_telegram_client)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class DraftCallbackAction:
    """Normalized inline-button action received from Telegram."""

    callback_query_id: str
    action: str
    draft_ref: str
    normalized: legacy.TelegramUpdateEvent


@router.post("/telegram/webhook")
async def sprint11b_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle Gate draft callbacks and delegate every other update to Sprint 10."""

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        return await sprint10.sprint10_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    callback_action = _extract_draft_callback_action(cast(JsonObject, raw_update))
    if callback_action is None:
        return await sprint10.sprint10_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    sprint9._require_telegram_secret(x_telegram_secret)
    bot, gate_event, duplicate = await sprint9._persist_admin_command_receipt(
        session=session,
        settings=settings,
        update=cast(JsonObject, raw_update),
        normalized=callback_action.normalized,
    )
    if duplicate:
        await _answer_callback_safely(
            telegram=telegram,
            callback_query_id=callback_action.callback_query_id,
            text="Уже обработано",
            session=session,
            bot_id=bot.id,
            gate_event_id=gate_event.id,
        )
        return {
            "ok": True,
            "event_type": callback_action.normalized.event_type,
            "auto_reply": None,
            "duplicate": True,
            "send_status": "duplicate",
        }

    if not legacy._is_telegram_admin_sender(callback_action.normalized.sender_id):
        await _answer_callback_safely(
            telegram=telegram,
            callback_query_id=callback_action.callback_query_id,
            text="Недоступно",
            show_alert=True,
            session=session,
            bot_id=bot.id,
            gate_event_id=gate_event.id,
        )
        await session.commit()
        return sprint9._telegram_result(text="Gate online")

    await _answer_callback_safely(
        telegram=telegram,
        callback_query_id=callback_action.callback_query_id,
        text="Обрабатываю",
        session=session,
        bot_id=bot.id,
        gate_event_id=gate_event.id,
    )

    if callback_action.action == "edit":
        result_text = await sprint10._send_draft_edit_link(
            request=request,
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            admin_chat_id=callback_action.normalized.chat_id,
            admin_business_connection_id=callback_action.normalized.business_connection_id,
            draft_ref=callback_action.draft_ref,
        )
    else:
        result_text = await sprint10._send_approved_draft_once(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            admin_chat_id=callback_action.normalized.chat_id,
            admin_business_connection_id=callback_action.normalized.business_connection_id,
            draft_ref=callback_action.draft_ref,
        )
        if result_text.startswith("Draft sent:"):
            await _clear_callback_keyboard_safely(
                telegram=telegram,
                callback_action=callback_action,
                session=session,
                bot_id=bot.id,
                gate_event_id=gate_event.id,
            )

    session.add(
        AuditLog(
            actor="gate",
            action=f"telegram_draft_{callback_action.action}_callback_handled",
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            metadata_={"draft_ref": callback_action.draft_ref},
        )
    )
    await session.commit()
    return sprint9._telegram_result(text=result_text)


def _extract_draft_callback_action(update: JsonObject) -> DraftCallbackAction | None:
    update_id = update.get("update_id")
    callback_query = update.get("callback_query")
    if type(update_id) is not int or not isinstance(callback_query, dict):
        return None

    callback_query_id = callback_query.get("id")
    data = callback_query.get("data")
    sender = callback_query.get("from")
    message = callback_query.get("message")
    if not isinstance(callback_query_id, str) or not isinstance(data, str):
        return None
    if not isinstance(sender, dict) or not isinstance(message, dict):
        return None

    sender_id = sender.get("id")
    message_id = message.get("message_id")
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if sender_id is None or chat_id is None:
        return None

    parts = data.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "gate":
        return None
    action, draft_ref = parts[1], parts[2].strip()
    if action not in {"edit", "send"} or len(draft_ref) < 4:
        return None

    business_connection_id = message.get("business_connection_id")
    normalized = legacy.TelegramUpdateEvent(
        update_id=update_id,
        message_id=message_id if isinstance(message_id, int) else None,
        event_type="callback_query",
        chat_id=str(chat_id),
        sender_id=str(sender_id),
        text=data,
        business_connection_id=(
            business_connection_id if isinstance(business_connection_id, str) else None
        ),
    )
    return DraftCallbackAction(
        callback_query_id=callback_query_id,
        action=action,
        draft_ref=draft_ref,
        normalized=normalized,
    )


async def _answer_callback_safely(
    *,
    telegram: TelegramClient,
    callback_query_id: str,
    text: str,
    session: AsyncSession,
    bot_id: Any,
    gate_event_id: Any,
    show_alert: bool = False,
) -> None:
    try:
        await telegram.answer_callback_query(
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_callback_answer_failed",
                bot_id=bot_id,
                gate_event_id=gate_event_id,
                metadata_={"error_type": exc.__class__.__name__},
            )
        )


async def _clear_callback_keyboard_safely(
    *,
    telegram: TelegramClient,
    callback_action: DraftCallbackAction,
    session: AsyncSession,
    bot_id: Any,
    gate_event_id: Any,
) -> None:
    message_id = callback_action.normalized.message_id
    if message_id is None:
        return
    try:
        await telegram.edit_message_reply_markup(
            chat_id=callback_action.normalized.chat_id,
            message_id=message_id,
            business_connection_id=callback_action.normalized.business_connection_id,
            reply_markup={"inline_keyboard": []},
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_draft_keyboard_clear_failed",
                bot_id=bot_id,
                gate_event_id=gate_event_id,
                metadata_={
                    "draft_ref": callback_action.draft_ref,
                    "message_id": message_id,
                    "error_type": exc.__class__.__name__,
                },
            )
        )
