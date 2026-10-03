# mypy: ignore-errors
"""Sprint 9 Telegram admin routes for approved draft sending."""

import hmac
import os
from datetime import UTC, datetime
from html import escape
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api import routes as legacy
from app.api.deps import get_db_session
from app.application.telegram_formatting import CAPTION_FORMAT_KEY, FORMAT_KEY
from app.core.config import Settings, get_settings
from app.models.domain import (
    AuditLog,
    Bot,
    Conversation,
    ConversationStatus,
    GateEvent,
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyDraftStatus,
    ReplyTask,
    ReplyTaskStatus,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(legacy.get_telegram_client)

JsonObject = dict[str, Any]


@router.post("/telegram/webhook")
async def sprint9_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Intercept Sprint 9 admin commands and delegate everything else to legacy."""

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        return await legacy.telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    normalized = legacy._extract_update_event(cast(JsonObject, raw_update))
    if normalized is None:
        return await legacy.telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    text = (normalized.text or "").strip()
    send_draft_ref = _extract_send_draft_ref(text)
    is_test_command = text.casefold() in {"/test", "test"}
    if send_draft_ref is None and not is_test_command:
        return await legacy.telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    _require_telegram_secret(x_telegram_secret)
    bot, gate_event, duplicate = await _persist_admin_command_receipt(
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
        await _send_admin_reply(
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
        return _telegram_result(text="Gate online")

    if is_test_command:
        guide = _format_sprint9_test_guide(
            commit_sha=settings.deployment_sha,
            bot_username=settings.telegram_bot_username,
        )
        await _send_admin_reply(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            chat_id=normalized.chat_id,
            business_connection_id=normalized.business_connection_id,
            text=guide,
            audit_action="telegram_admin_console_test_sent",
            metadata={"command": "test", "sprint": 9},
        )
        await session.commit()
        return _telegram_result(text=guide)

    send_result_text = await _send_approved_draft(
        session=session,
        telegram=telegram,
        settings=settings,
        bot=bot,
        gate_event=gate_event,
        admin_chat_id=normalized.chat_id,
        admin_business_connection_id=normalized.business_connection_id,
        draft_ref=send_draft_ref or "",
    )
    await session.commit()
    return _telegram_result(text=send_result_text)


async def _persist_admin_command_receipt(
    *,
    session: AsyncSession,
    settings: Settings,
    update: JsonObject,
    normalized: legacy.TelegramUpdateEvent,
) -> tuple[Bot, GateEvent, bool]:
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
        return bot, GateEvent(), True

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
    session.add(gate_event)
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
    session.add(
        Message(
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            direction=MessageDirection.INBOUND.value,
            source="telegram",
            event_type=normalized.event_type,
            chat_id=normalized.chat_id,
            sender_id=normalized.sender_id,
            text=normalized.text,
            telegram_update_id=normalized.update_id,
            telegram_message_id=normalized.message_id,
            business_connection_id=normalized.business_connection_id,
            payload={"raw_update_ref": "telegram_updates"},
            received_at=datetime.now(UTC),
        )
    )
    session.add(
        AuditLog(
            actor="telegram",
            action="telegram_update_received",
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            metadata_={
                "event_type": normalized.event_type,
                "telegram_update_id": normalized.update_id,
                "telegram_message_id": normalized.message_id,
            },
        )
    )
    return bot, gate_event, False


async def _send_approved_draft(
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
    rows = await _find_reply_draft_rows_by_ref(session=session, draft_ref=draft_ref)
    if not rows:
        text = f"Draft not found: {draft_ref}"
        await _send_admin_reply(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            chat_id=admin_chat_id,
            business_connection_id=admin_business_connection_id,
            text=text,
            audit_action="reply_draft_send_not_found",
            metadata={"draft_ref": draft_ref},
        )
        return text
    if len(rows) > 1:
        text = f"Draft reference is ambiguous: {draft_ref}"
        await _send_admin_reply(
            session=session,
            telegram=telegram,
            settings=settings,
            bot=bot,
            gate_event=gate_event,
            chat_id=admin_chat_id,
            business_connection_id=admin_business_connection_id,
            text=text,
            audit_action="reply_draft_send_ambiguous",
            metadata={"draft_ref": draft_ref},
        )
        return text

    draft, task, conversation = rows[0]
    if (
        FORMAT_KEY in (draft.prompt_context or {})
        or CAPTION_FORMAT_KEY in (draft.prompt_context or {})
    ):
        return ("This draft requires its saved formatting preview "
                "and hash confirmation through Gate MCP.")
    sent_at = datetime.now(UTC)
    client_send_result = await telegram.send_message(
        chat_id=conversation.chat_id,
        text=draft.draft_text,
        business_connection_id=conversation.business_connection_id,
    )
    session.add(
        Message(
            bot_id=conversation.bot_id,
            gate_event_id=gate_event.id,
            direction=MessageDirection.OUTBOUND.value,
            source="gate",
            event_type="approved_draft_reply",
            chat_id=conversation.chat_id,
            sender_id=settings.telegram_bot_username,
            recipient_id=conversation.chat_id,
            text=draft.draft_text,
            business_connection_id=conversation.business_connection_id,
            payload=client_send_result,
            sent_at=sent_at,
        )
    )
    old_task_status = task.status
    draft.status = ReplyDraftStatus.APPROVED.value
    task.status = ReplyTaskStatus.REPLIED.value
    task.due_at = None
    conversation.status = ConversationStatus.REPLIED.value
    conversation.requires_reply = False
    conversation.last_outbound_at = sent_at
    conversation.last_message_at = sent_at
    session.add(
        AuditLog(
            actor="gate",
            action="reply_draft_sent_from_telegram",
            bot_id=conversation.bot_id,
            gate_event_id=gate_event.id,
            metadata_={
                "draft_id": str(draft.id),
                "task_id": str(task.id),
                "from_task_status": old_task_status,
                "to_task_status": task.status,
            },
        )
    )
    text = f"Draft sent: {str(draft.id)[:8]}\ntask: {str(task.id)[:8]}"
    await _send_admin_reply(
        session=session,
        telegram=telegram,
        settings=settings,
        bot=bot,
        gate_event=gate_event,
        chat_id=admin_chat_id,
        business_connection_id=admin_business_connection_id,
        text=text,
        audit_action="telegram_admin_console_send_sent",
        metadata={"draft_id": str(draft.id), "task_id": str(task.id)},
    )
    return text


async def _find_reply_draft_rows_by_ref(
    *, session: AsyncSession, draft_ref: str
) -> list[tuple[ReplyDraft, ReplyTask, Conversation]]:
    result = await session.execute(
        select(ReplyDraft, ReplyTask, Conversation)
        .join(ReplyTask, ReplyDraft.reply_task_id == ReplyTask.id)
        .join(Conversation, ReplyDraft.conversation_id == Conversation.id)
        .where(sql_cast(ReplyDraft.id, String).like(f"{draft_ref}%"))
        .order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .limit(2)
    )
    return [(draft, task, conversation) for draft, task, conversation in result.all()]


async def _send_admin_reply(
    *,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
    bot: Bot,
    gate_event: GateEvent,
    chat_id: str,
    business_connection_id: str | None,
    text: str,
    audit_action: str,
    metadata: JsonObject,
) -> None:
    send_result = await telegram.send_message(
        chat_id=chat_id,
        text=text,
        business_connection_id=business_connection_id,
    )
    session.add(
        Message(
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            direction=MessageDirection.OUTBOUND.value,
            source="gate",
            event_type="telegram_admin_console_reply",
            chat_id=chat_id,
            sender_id=settings.telegram_bot_username,
            recipient_id=chat_id,
            text=text,
            business_connection_id=business_connection_id,
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
            metadata_=metadata,
        )
    )


def _format_sprint9_test_guide(*, commit_sha: str, bot_username: str) -> str:
    short_sha = commit_sha[:12] if commit_sha != "unknown" else commit_sha
    return "\n".join(
        [
            "Gate test: draft flow",
            f"Commit: {escape(short_sha)}",
            f"Bot: {escape(bot_username)}",
            "Task: &lt;pending&gt;",
            "",
            "1. Copy and send text message:",
            f"<pre>{escape(legacy.TEST_PROBE_TEXT)}</pre>",
            "Expected: this message is edited with the real task id.",
            "",
            "2. Press /inbox",
            "Expected: task appears with clickable actions.",
            "",
            "3. Press /draft_&lt;task&gt; from /inbox",
            "Expected: Draft created ... Not sent.",
            "",
            "4. Press /drafts_&lt;task&gt;",
            "Expected: saved draft history appears.",
            "",
            "5. Press /send_&lt;draft&gt;",
            "Expected: Draft sent ... task replied.",
            "",
            "6. Press /inbox",
            "Expected: No unanswered messages.",
        ]
    )


def _extract_send_draft_ref(text: str) -> str | None:
    normalized = text.strip().casefold()
    for prefix in ("/send_", "/senddraft_"):
        if normalized.startswith(prefix):
            return normalized.removeprefix(prefix).strip()
    parts = normalized.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in {"/send", "send", "/senddraft", "senddraft"}:
        return parts[1].strip()
    return None


def _telegram_result(*, text: str) -> JsonObject:
    return {
        "ok": True,
        "event_type": "message",
        "auto_reply": text,
        "duplicate": False,
        "send_status": "sent",
    }


def _require_telegram_secret(x_telegram_secret: str | None) -> None:
    configured_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not configured_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook secret is not configured",
        )
    invalid_secret = x_telegram_secret is None or not hmac.compare_digest(
        x_telegram_secret,
        configured_secret,
    )
    if invalid_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret",
        )


_original_format_reply_draft_result = legacy._format_reply_draft_result
_original_format_reply_drafts_result = legacy._format_reply_drafts_result


def _format_reply_draft_result_with_send(*, draft: ReplyDraft, task: ReplyTask) -> str:
    return "\n".join(
        [
            _original_format_reply_draft_result(draft=draft, task=task),
            f"Approve send: /send_{str(draft.id)[:8]}",
        ]
    )


def _format_reply_drafts_result_with_send(*, task: ReplyTask, drafts: list[ReplyDraft]) -> str:
    result = _original_format_reply_drafts_result(task=task, drafts=drafts)
    if not drafts:
        return result
    send_lines = ["", "Send commands:"]
    send_lines.extend(f"/send_{str(draft.id)[:8]}" for draft in drafts)
    return "\n".join([result, *send_lines])


legacy._format_reply_draft_result = _format_reply_draft_result_with_send
legacy._format_reply_drafts_result = _format_reply_drafts_result_with_send
