# mypy: ignore-errors
"""Sprint 10 Telegram admin routes for external draft editing."""

import hashlib
import hmac
import os
from html import escape
from typing import Annotated, Any, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint9_routes as sprint9
from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import (
    AuditLog,
    Bot,
    GateEvent,
    Message,
    MessageDirection,
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
    """Intercept draft edit commands and delegate everything else to Sprint 9."""

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        return await sprint9.sprint9_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    normalized = legacy._extract_update_event(cast(JsonObject, raw_update))
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
        update=cast(JsonObject, raw_update),
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
    telegram: TelegramClient = telegram_client_dependency,
) -> HTMLResponse:
    """Persist edited draft text from the external form and refresh the Telegram draft card."""

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
    await _refresh_saved_draft_card(
        session=session,
        telegram=telegram,
        draft=draft,
        task=task,
    )
    await session.commit()
    return HTMLResponse(_render_draft_saved_page(draft=draft, task=task))


async def _refresh_saved_draft_card(
    *,
    session: AsyncSession,
    telegram: TelegramClient,
    draft: ReplyDraft,
    task: ReplyTask,
) -> None:
    """Edit the original Telegram draft card after the external form is saved."""

    card_message = await _find_latest_draft_card_message(session=session, draft=draft)
    if card_message is None:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_draft_card_refresh_skipped",
                bot_id=None,
                gate_event_id=None,
                metadata_={
                    "draft_id": str(draft.id),
                    "reason": "draft_card_not_found",
                },
            )
        )
        return

    telegram_message_id = _extract_telegram_message_id(card_message.payload)
    if telegram_message_id is None:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_draft_card_refresh_skipped",
                bot_id=card_message.bot_id,
                gate_event_id=card_message.gate_event_id,
                metadata_={
                    "draft_id": str(draft.id),
                    "message_id": str(card_message.id),
                    "reason": "telegram_message_id_not_found",
                },
            )
        )
        return

    updated_text = _format_reply_draft_result_with_edit(draft=draft, task=task)
    try:
        edit_result = await telegram.edit_message_text(
            chat_id=card_message.chat_id,
            message_id=telegram_message_id,
            text=updated_text,
            business_connection_id=card_message.business_connection_id,
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_draft_card_refresh_failed",
                bot_id=card_message.bot_id,
                gate_event_id=card_message.gate_event_id,
                metadata_={
                    "draft_id": str(draft.id),
                    "message_id": str(card_message.id),
                    "telegram_message_id": telegram_message_id,
                    "error_type": exc.__class__.__name__,
                },
            )
        )
        return

    card_message.text = updated_text
    card_message.payload = {
        "original_send": card_message.payload,
        "latest_edit": edit_result,
    }
    card_message.telegram_message_id = telegram_message_id
    session.add(
        AuditLog(
            actor="gate",
            action="telegram_draft_card_refreshed",
            bot_id=card_message.bot_id,
            gate_event_id=card_message.gate_event_id,
            metadata_={
                "draft_id": str(draft.id),
                "message_id": str(card_message.id),
                "telegram_message_id": telegram_message_id,
            },
        )
    )


async def _find_latest_draft_card_message(
    *,
    session: AsyncSession,
    draft: ReplyDraft,
) -> Message | None:
    draft_ref = str(draft.id)[:8]
    result = await session.execute(
        select(Message)
        .where(
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.source == "gate",
            Message.event_type == "telegram_admin_console_reply",
            Message.text.like(f"Draft created: {draft_ref}%"),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return cast(Message | None, result.scalar_one_or_none())


def _extract_telegram_message_id(payload: JsonObject | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("latest_edit")
    if isinstance(result, dict):
        nested_result = result.get("result")
        if isinstance(nested_result, dict):
            value = nested_result.get("message_id")
            if isinstance(value, int):
                return value
    result = payload.get("result")
    if isinstance(result, dict):
        value = result.get("message_id")
        if isinstance(value, int):
            return value
    original_send = payload.get("original_send")
    if isinstance(original_send, dict):
        return _extract_telegram_message_id(cast(JsonObject, original_send))
    return None


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
