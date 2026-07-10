# mypy: ignore-errors
"""Sprint 10 Telegram admin routes for external draft editing."""

import hashlib
import hmac
import os
from datetime import UTC, datetime
from html import escape
from typing import Annotated, Any, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint9_routes as sprint9
from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, Bot, GateEvent, ReplyDraft, ReplyTask
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


_original_format_reply_draft_result = legacy._format_reply_draft_result
_original_format_reply_drafts_result = legacy._format_reply_drafts_result


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


legacy._format_reply_draft_result = _format_reply_draft_result_with_edit
legacy._format_reply_drafts_result = _format_reply_drafts_result_with_edit
