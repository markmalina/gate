# mypy: ignore-errors
"""Sprint 11b Telegram inline actions for draft editing and sending."""

from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api import routes as legacy
from app.api import sprint9_routes as sprint9
from app.api import sprint10_routes as sprint10
from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, ReplyDraft, ReplyTask
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(legacy.get_telegram_client)

JsonObject = dict[str, Any]
CALLBACK_PREFIX = "gate"
CALLBACK_EDIT = "edit"
CALLBACK_SEND = "send"


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
    """Handle Gate draft callbacks and delegate every non-callback update to Sprint 10."""

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        return await _delegate_to_sprint10(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    callback_action = _extract_draft_callback_action(cast(JsonObject, raw_update))
    if callback_action is None:
        return await _delegate_to_sprint10(
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
        return _callback_result(
            action=callback_action,
            text=None,
            duplicate=True,
            send_status="duplicate",
        )

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
        return _callback_result(
            action=callback_action,
            text="Gate online",
            send_status="rejected",
        )

    await _answer_callback_safely(
        telegram=telegram,
        callback_query_id=callback_action.callback_query_id,
        text="Обрабатываю",
        session=session,
        bot_id=bot.id,
        gate_event_id=gate_event.id,
    )

    if callback_action.action == CALLBACK_EDIT:
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
        send_status = "edit_link_sent"
    else:
        await _lock_draft_and_task_by_ref(
            session=session,
            draft_ref=callback_action.draft_ref,
        )
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
        send_status = "sent" if result_text.startswith("Draft sent:") else "rejected"
        if send_status == "sent":
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
            metadata_={
                "draft_ref": callback_action.draft_ref,
                "callback_query_id": callback_action.callback_query_id,
                "send_status": send_status,
            },
        )
    )
    await session.commit()
    return _callback_result(
        action=callback_action,
        text=result_text,
        send_status=send_status,
    )


async def _delegate_to_sprint10(
    *,
    request: Request,
    x_telegram_secret: str | None,
    settings: Settings,
    session: AsyncSession,
    telegram: TelegramClient,
) -> JsonObject:
    return await sprint10.sprint10_telegram_webhook(
        request=request,
        x_telegram_secret=x_telegram_secret,
        settings=settings,
        session=session,
        telegram=telegram,
    )


def _callback_result(
    *,
    action: DraftCallbackAction,
    text: str | None,
    send_status: str,
    duplicate: bool = False,
) -> JsonObject:
    return {
        "ok": True,
        "event_type": action.normalized.event_type,
        "auto_reply": text,
        "duplicate": duplicate,
        "send_status": send_status,
    }


async def _lock_draft_and_task_by_ref(
    *,
    session: AsyncSession,
    draft_ref: str,
) -> None:
    """Serialize send callbacks for the same draft until the transaction commits."""

    await session.execute(
        select(ReplyDraft, ReplyTask)
        .join(ReplyTask, ReplyDraft.reply_task_id == ReplyTask.id)
        .where(sql_cast(ReplyDraft.id, String).like(f"{draft_ref}%"))
        .order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .limit(2)
        .with_for_update()
    )


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
    chat_id = _extract_callback_chat_id(message)
    if sender_id is None or chat_id is None:
        return None

    parsed = _parse_callback_data(data)
    if parsed is None:
        return None
    action, draft_ref = parsed

    message_id = message.get("message_id")
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


def _parse_callback_data(data: str) -> tuple[str, str] | None:
    parts = data.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None

    action, draft_ref = parts[1], parts[2].strip()
    if action not in {CALLBACK_EDIT, CALLBACK_SEND} or len(draft_ref) < 4:
        return None
    return action, draft_ref


def _extract_callback_chat_id(message: JsonObject) -> Any | None:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    return chat.get("id")


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
                metadata_={
                    "callback_query_id": callback_query_id,
                    "error_type": exc.__class__.__name__,
                },
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
