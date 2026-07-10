# mypy: ignore-errors
"""Sprint 10 Telegram admin routes for external draft editing."""

import hashlib
import hmac
import os
from dataclasses import dataclass
from html import escape
from typing import Annotated, Any, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api import routes as legacy
from app.api import sprint9_routes as sprint9
from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import (
    AuditLog,
    Bot,
    GateEvent,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(legacy.get_telegram_client)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class DraftCallbackAction:
    """Normalized inline draft action received from Telegram."""

    callback_query_id: str
    action: str
    draft_ref: str
    normalized: legacy.TelegramUpdateEvent


@router.post("/telegram/webhook")
async def sprint10_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle draft callbacks/edit commands and delegate everything else to Sprint 9."""

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        return await sprint9.sprint9_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    update = cast(JsonObject, raw_update)
    callback_action = _extract_draft_callback_action(update)
    if callback_action is not None:
        return await _handle_draft_callback(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
            update=update,
            callback_action=callback_action,
        )
    if isinstance(update.get("callback_query"), dict):
        return await _acknowledge_unknown_callback(
            update=update,
            x_telegram_secret=x_telegram_secret,
            telegram=telegram,
        )

    normalized = legacy._extract_update_event(update)
    if normalized is None:
        return await sprint9.sprint9_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    text = (normalized.text or "").strip()
    draft_ref = _extract_edit_draft_ref(text)
    if draft_ref is None:
        return await sprint9.sprint9_telegram_webhook(
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
        update=update,
        normalized=normalized,
    )
    if duplicate:
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": None,
            "duplicate": True,
            "send_status": "duplicate",
        }

    if not legacy._is_telegram_admin_sender(normalized.sender_id):
        await sprint9._send_admin_reply(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            chat_id=normalized.chat_id,
            business_connection_id=normalized.business_connection_id,
            text="Gate online",
            audit_action="telegram_admin_console_rejected",
            metadata={"command": text},
        )
        await session.commit()
        return sprint9._telegram_result(text="Gate online")

    reply_text = await _send_draft_edit_link(
        request=request,
        session=session,
        telegram=telegram,
        settings=settings,
        bot=bot,
        gate_event=gate_event,
        admin_chat_id=normalized.chat_id,
        admin_business_connection_id=normalized.business_connection_id,
        draft_ref=draft_ref,
    )
    await session.commit()
    return sprint9._telegram_result(text=reply_text)


async def _handle_draft_callback(
    *,
    request: Request,
    x_telegram_secret: str | None,
    settings: Settings,
    session: AsyncSession,
    telegram: TelegramClient,
    update: JsonObject,
    callback_action: DraftCallbackAction,
) -> JsonObject:
    sprint9._require_telegram_secret(x_telegram_secret)
    bot, gate_event, duplicate = await sprint9._persist_admin_command_receipt(
        session=session,
        settings=settings,
        update=update,
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
            "event_type": "callback_query",
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
        result_text = await _send_draft_edit_link(
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
        await _lock_draft_and_task_by_ref(
            session=session,
            draft_ref=callback_action.draft_ref,
        )
        result_text = await _send_approved_draft_once(
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


async def _acknowledge_unknown_callback(
    *,
    update: JsonObject,
    x_telegram_secret: str | None,
    telegram: TelegramClient,
) -> JsonObject:
    sprint9._require_telegram_secret(x_telegram_secret)
    callback_query = update.get("callback_query")
    callback_query_id = (
        callback_query.get("id") if isinstance(callback_query, dict) else None
    )
    if isinstance(callback_query_id, str):
        try:
            await telegram.answer_callback_query(
                callback_query_id=callback_query_id,
                text="Действие недоступно",
                show_alert=True,
            )
        except Exception:
            pass
    return {
        "ok": True,
        "event_type": "callback_query",
        "auto_reply": None,
        "duplicate": False,
        "send_status": "ignored",
    }


def _extract_draft_callback_action(update: JsonObject) -> DraftCallbackAction | None:
    update_id = legacy._optional_int(update.get("update_id"))
    callback_query = update.get("callback_query")
    if update_id is None or not isinstance(callback_query, dict):
        return None

    callback_query_id = callback_query.get("id")
    data = callback_query.get("data")
    sender = callback_query.get("from")
    message = callback_query.get("message")
    if not isinstance(callback_query_id, str) or not isinstance(data, str):
        return None
    if not isinstance(sender, dict) or not isinstance(message, dict):
        return None

    message_id = legacy._optional_int(message.get("message_id"))
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = legacy._optional_string(chat.get("id"))
    sender_id = legacy._optional_string(sender.get("id"))
    if chat_id is None or sender_id is None:
        return None

    parts = data.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "gate":
        return None
    action, draft_ref = parts[1], parts[2].strip()
    if action not in {"edit", "send"} or len(draft_ref) < 4:
        return None

    normalized = legacy.TelegramUpdateEvent(
        update_id=update_id,
        message_id=message_id,
        event_type="callback_query",
        chat_id=chat_id,
        sender_id=sender_id,
        text=data,
        business_connection_id=legacy._optional_string(
            message.get("business_connection_id")
        ),
    )
    return DraftCallbackAction(
        callback_query_id=callback_query_id,
        action=action,
        draft_ref=draft_ref,
        normalized=normalized,
    )


async def _lock_draft_and_task_by_ref(
    *,
    session: AsyncSession,
    draft_ref: str,
) -> None:
    await session.execute(
        select(ReplyDraft, ReplyTask)
        .join(ReplyTask, ReplyDraft.reply_task_id == ReplyTask.id)
        .where(sql_cast(ReplyDraft.id, String).like(f"{draft_ref}%"))
        .order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .limit(2)
        .with_for_update()
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


@router.get("/admin/drafts/{draft_ref}/edit", response_class=HTMLResponse)
async def edit_draft_form(
    draft_ref: str,
    token: str,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> HTMLResponse:
    """Render a protected external form for editing a stored draft."""

    await _require_valid_draft_token(draft_ref=draft_ref, token=token, settings=settings)
    draft, task = await _load_single_draft_for_edit(session=session, draft_ref=draft_ref)
    return HTMLResponse(_render_draft_edit_form(draft=draft, task=task, token=token))


@router.post("/admin/drafts/{draft_ref}/edit", response_class=HTMLResponse)
async def save_draft_form(
    draft_ref: str,
    token: str,
    request: Request,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> HTMLResponse:
    """Persist edited draft text from the external form."""

    await _require_valid_draft_token(draft_ref=draft_ref, token=token, settings=settings)
    draft, task = await _load_single_draft_for_edit(session=session, draft_ref=draft_ref)
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body, keep_blank_values=True)
    draft_text = form.get("draft_text", [""])[0].strip()
    if not draft_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Draft text is empty")
    if len(draft_text) > 4096:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Draft text is too long")

    draft.draft_text = draft_text
    draft.status = ReplyDraftStatus.EDITED.value
    session.add(
        AuditLog(
            actor="gate",
            action="reply_draft_edited_from_external_form",
            bot_id=None,
            gate_event_id=None,
            metadata_={"draft_id": str(draft.id), "task_id": str(task.id)},
        )
    )
    await session.commit()
    return HTMLResponse(_render_draft_saved_page(draft=draft, task=task))


async def _send_draft_edit_link(
    *,
    request: Request,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
    bot: Bot,
    gate_event: GateEvent,
    admin_chat_id: str,
    admin_business_connection_id: str | None,
    draft_ref: str,
) -> str:
    rows = await sprint9._find_reply_draft_rows_by_ref(session=session, draft_ref=draft_ref)
    if not rows:
        text = f"Draft not found: {draft_ref}"
    elif len(rows) > 1:
        text = f"Draft reference is ambiguous: {draft_ref}"
    else:
        draft, task, _conversation = rows[0]
        if not _is_draft_open(draft=draft, task=task):
            text = _closed_draft_message(action="edited", draft=draft, task=task)
        else:
            full_ref = str(draft.id)
            token = _draft_edit_token(draft_ref=full_ref, settings=settings)
            base_url = str(request.base_url).rstrip("/")
            edit_url = f"{base_url}/admin/drafts/{full_ref}/edit?token={token}"
            text = "\n".join(
                [
                    f"Edit draft: {str(draft.id)[:8]}",
                    edit_url,
                    "",
                    f"After save: /send_{str(draft.id)[:8]}",
                    f"Task: {str(task.id)[:8]}",
                ]
            )
    await sprint9._send_admin_reply(
        session=session,
        telegram=telegram,
        settings=settings,
        bot=bot,
        gate_event=gate_event,
        chat_id=admin_chat_id,
        business_connection_id=admin_business_connection_id,
        text=text,
        audit_action="telegram_admin_console_edit_link_sent",
        metadata={"draft_ref": draft_ref},
    )
    return text


async def _load_single_draft_for_edit(
    *, session: AsyncSession, draft_ref: str
) -> tuple[ReplyDraft, ReplyTask]:
    rows = await sprint9._find_reply_draft_rows_by_ref(session=session, draft_ref=draft_ref)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    if len(rows) > 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Draft reference is ambiguous")
    draft, task, _conversation = rows[0]
    if not _is_draft_open(draft=draft, task=task):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_closed_draft_message(action="edited", draft=draft, task=task),
        )
    return draft, task


async def _require_valid_draft_token(*, draft_ref: str, token: str, settings: Settings) -> None:
    expected = _draft_edit_token(draft_ref=draft_ref, settings=settings)
    if not expected or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid draft edit token")


def _draft_edit_token(*, draft_ref: str, settings: Settings) -> str:
    secret = settings.admin_bearer_token or os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        return ""
    return hmac.new(secret.encode(), draft_ref.encode(), hashlib.sha256).hexdigest()[:40]


def _extract_edit_draft_ref(text: str) -> str | None:
    normalized = text.strip().casefold()
    for prefix in ("/edit_", "/editdraft_"):
        if normalized.startswith(prefix):
            return normalized.removeprefix(prefix).strip()
    parts = normalized.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in {"/edit", "edit", "/editdraft", "editdraft"}:
        return parts[1].strip()
    return None


def _is_draft_open(*, draft: ReplyDraft, task: ReplyTask) -> bool:
    return draft.status in {
        ReplyDraftStatus.GENERATED.value,
        ReplyDraftStatus.EDITED.value,
    } and task.status == ReplyTaskStatus.NEEDS_REPLY.value


def _closed_draft_message(*, action: str, draft: ReplyDraft, task: ReplyTask) -> str:
    return "\n".join(
        [
            f"Draft cannot be {action}: {str(draft.id)[:8]}",
            f"draft status: {draft.status}",
            f"task status: {task.status}",
        ]
    )


def _render_draft_edit_form(*, draft: ReplyDraft, task: ReplyTask, token: str) -> str:
    action = f"/admin/drafts/{escape(str(draft.id))}/edit?token={escape(token)}"
    return f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Edit Gate draft</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; max-width: 760px; }}
    textarea {{ width: 100%; min-height: 260px; font-size: 16px; line-height: 1.4; }}
    button {{ font-size: 16px; padding: 10px 16px; margin-top: 12px; }}
    .meta {{ color: #555; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <h1>Edit draft</h1>
  <div class="meta">draft: {escape(str(draft.id)[:8])} · task: {escape(str(task.id)[:8])}</div>
  <form method="post" action="{action}">
    <textarea name="draft_text">{escape(draft.draft_text)}</textarea>
    <br>
    <button type="submit">Save draft</button>
  </form>
</body>
</html>
"""


def _render_draft_saved_page(*, draft: ReplyDraft, task: ReplyTask) -> str:
    draft_ref = escape(str(draft.id)[:8])
    task_ref = escape(str(task.id)[:8])
    return f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gate draft saved</title>
  <style>body {{ font-family: system-ui, sans-serif; margin: 24px; max-width: 760px; }}</style>
</head>
<body>
  <h1>Draft saved</h1>
  <p>draft: {draft_ref}</p>
  <p>task: {task_ref}</p>
  <p>Go back to Telegram and press:</p>
  <pre>/send_{draft_ref}</pre>
</body>
</html>
"""


_original_send_approved_draft = sprint9._send_approved_draft
_original_format_reply_draft_result = legacy._format_reply_draft_result
_original_format_reply_drafts_result = legacy._format_reply_drafts_result


async def _send_approved_draft_once(
    *,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
    bot: Bot,
    gate_event: GateEvent,
    admin_chat_id: str,
    admin_business_connection_id: str | None,
    draft_ref: str,
) -> str:
    rows = await sprint9._find_reply_draft_rows_by_ref(session=session, draft_ref=draft_ref)
    if len(rows) != 1:
        return await _original_send_approved_draft(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            admin_chat_id=admin_chat_id,
            admin_business_connection_id=admin_business_connection_id,
            draft_ref=draft_ref,
        )

    draft, task, _conversation = rows[0]
    if _is_draft_open(draft=draft, task=task):
        return await _original_send_approved_draft(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            admin_chat_id=admin_chat_id,
            admin_business_connection_id=admin_business_connection_id,
            draft_ref=draft_ref,
        )

    text = _closed_draft_message(action="sent", draft=draft, task=task)
    await sprint9._send_admin_reply(
        session=session,
        telegram=telegram,
        settings=settings,
        bot=bot,
        gate_event=gate_event,
        chat_id=admin_chat_id,
        business_connection_id=admin_business_connection_id,
        text=text,
        audit_action="reply_draft_send_rejected_closed",
        metadata={
            "draft_id": str(draft.id),
            "task_id": str(task.id),
            "draft_status": draft.status,
            "task_status": task.status,
        },
    )
    return text


def _format_reply_draft_result_with_edit(*, draft: ReplyDraft, task: ReplyTask) -> str:
    return "\n".join(
        [
            _original_format_reply_draft_result(draft=draft, task=task),
            f"Edit draft: /edit_{str(draft.id)[:8]}",
        ]
    )


def _format_reply_drafts_result_with_edit(*, task: ReplyTask, drafts: list[ReplyDraft]) -> str:
    result = _original_format_reply_drafts_result(task=task, drafts=drafts)
    if not drafts:
        return result
    edit_lines = ["", "Edit commands:"]
    edit_lines.extend(f"/edit_{str(draft.id)[:8]}" for draft in drafts)
    return "\n".join([result, *edit_lines])


sprint9._send_approved_draft = _send_approved_draft_once
legacy._format_reply_draft_result = _format_reply_draft_result_with_edit
legacy._format_reply_drafts_result = _format_reply_drafts_result_with_edit
