# mypy: ignore-errors
# ruff: noqa
"""Sprint 17 Telegram draft approval and send commands."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import String, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api import sprint16d_clean_inbox_routes as sprint16d
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.scoped_admin import resolve_telegram_admin_context
from app.core.config import Settings, get_settings
from app.models.domain import (
    Account,
    AuditLog,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    ReplyDraft,
    ReplyDraftStatus,
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
async def sprint17_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle manual draft send/approval before delegating other updates."""

    update = await sprint14._read_update(request)
    normalized = legacy._extract_update_event(update)
    draft_ref = _send_draft_ref(normalized.text if normalized else None)
    if normalized is None or draft_ref is None:
        return await sprint16d.sprint16d_telegram_webhook(
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
        reply_text = await _send_draft_reply(
            session=session,
            telegram=telegram,
            settings=settings,
            draft_ref=draft_ref,
            account_id=admin_context.scoped_account_id,
            scope_label="all" if admin_context.is_super_admin else admin_context.account_alias or "unknown",
        )
        audit_action = "telegram_admin_console_send_draft_sent"
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
                "draft_ref": draft_ref,
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


async def _send_draft_reply(
    *,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
    draft_ref: str,
    account_id: Any | None,
    scope_label: str,
) -> str:
    if not settings.gate_send_enabled:
        return _send_result_text(settings=settings, scope_label=scope_label, status="blocked", detail="global send disabled")

    rows = await _load_draft_rows_by_ref(
        session=session,
        draft_ref=draft_ref,
        account_id=account_id,
    )
    if not rows:
        return _send_result_text(settings=settings, scope_label=scope_label, status="not_found", detail=f"draft not found: {draft_ref}")
    if len(rows) > 1:
        return _send_result_text(settings=settings, scope_label=scope_label, status="ambiguous", detail=f"draft reference is ambiguous: {draft_ref}")

    draft, task, conversation, trigger_message = rows[0]
    if draft.status == ReplyDraftStatus.APPROVED.value:
        return _send_result_text(settings=settings, scope_label=scope_label, status="already_sent", detail=f"draft already approved: {str(draft.id)[:8]}")

    connection, account = await _resolve_send_connection(
        session=session,
        account_id=account_id,
        conversation=conversation,
        trigger_message=trigger_message,
    )
    if connection is None or account is None:
        return _send_result_text(settings=settings, scope_label=scope_label, status="blocked", detail="active send connection not found")
    if not _account_send_enabled(settings=settings, account_alias=account.alias):
        return _send_result_text(settings=settings, scope_label=scope_label, status="blocked", detail=f"send disabled for {account.alias}")
    if connection.rights.get("can_reply") is False:
        return _send_result_text(settings=settings, scope_label=scope_label, status="blocked", detail="business connection can_reply=false")

    target_chat_id = _resolve_target_chat_id(
        trigger_message=trigger_message,
        connection=connection,
    )
    try:
        telegram_result = await telegram.send_message(
            chat_id=target_chat_id,
            text=draft.draft_text,
            business_connection_id=connection.business_connection_id,
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="reply_draft_send_failed",
                bot_id=conversation.bot_id,
                gate_event_id=None,
                account_id=conversation.account_id,
                project_id=conversation.project_id,
                metadata_={
                    "draft_id": str(draft.id),
                    "task_id": str(task.id),
                    "target_chat_id": target_chat_id,
                    "business_connection_id": connection.business_connection_id,
                    "error_type": exc.__class__.__name__,
                },
            )
        )
        return _send_result_text(settings=settings, scope_label=scope_label, status="failed", detail=exc.__class__.__name__)

    now = datetime.now(UTC)
    draft.status = ReplyDraftStatus.APPROVED.value
    task.status = ReplyTaskStatus.REPLIED.value
    task.due_at = None
    conversation.status = ConversationStatus.REPLIED.value
    conversation.requires_reply = False
    conversation.last_outbound_at = now
    conversation.last_message_at = now
    session.add(
        Message(
            bot_id=conversation.bot_id,
            gate_event_id=None,
            account_id=connection.account_id,
            project_id=conversation.project_id,
            direction=MessageDirection.OUTBOUND.value,
            source="gate-approved-draft",
            event_type="telegram_business_reply",
            chat_id=target_chat_id,
            sender_id="gate",
            recipient_id=target_chat_id,
            text=draft.draft_text,
            business_connection_id=connection.business_connection_id,
            payload=telegram_result,
            sent_at=now,
        )
    )
    session.add(
        AuditLog(
            actor="gate",
            action="reply_draft_sent_from_telegram",
            bot_id=conversation.bot_id,
            gate_event_id=None,
            account_id=connection.account_id,
            project_id=conversation.project_id,
            metadata_={
                "draft_id": str(draft.id),
                "task_id": str(task.id),
                "target_chat_id": target_chat_id,
                "business_connection_id": connection.business_connection_id,
            },
        )
    )
    return _send_result_text(
        settings=settings,
        scope_label=scope_label,
        status="sent",
        detail=f"draft {str(draft.id)[:8]} → chat {target_chat_id}",
    )


async def _load_draft_rows_by_ref(
    *,
    session: AsyncSession,
    draft_ref: str,
    account_id: Any | None,
) -> list[tuple[ReplyDraft, ReplyTask, Conversation, Message]]:
    query = (
        select(ReplyDraft, ReplyTask, Conversation, Message)
        .join(ReplyTask, ReplyDraft.reply_task_id == ReplyTask.id)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(sql_cast(ReplyDraft.id, String).like(f"{draft_ref}%"))
        .order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .limit(2)
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
    return [(draft, task, conversation, message) for draft, task, conversation, message in result.all()]


async def _resolve_send_connection(
    *,
    session: AsyncSession,
    account_id: Any | None,
    conversation: Conversation,
    trigger_message: Message,
) -> tuple[TelegramBusinessConnection | None, Account | None]:
    if account_id is not None:
        result = await session.execute(
            select(TelegramBusinessConnection, Account)
            .join(Account, TelegramBusinessConnection.account_id == Account.id)
            .where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.is_enabled.is_(True),
            )
            .order_by(TelegramBusinessConnection.last_update_at.desc())
            .limit(1)
        )
        return result.one_or_none() or (None, None)

    business_connection_id = conversation.business_connection_id or trigger_message.business_connection_id
    if not business_connection_id:
        return None, None
    result = await session.execute(
        select(TelegramBusinessConnection, Account)
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .where(
            TelegramBusinessConnection.business_connection_id == business_connection_id,
            TelegramBusinessConnection.is_enabled.is_(True),
        )
        .limit(1)
    )
    return result.one_or_none() or (None, None)


def _resolve_target_chat_id(*, trigger_message: Message, connection: TelegramBusinessConnection) -> str:
    owner_chat_id = str(connection.user_chat_id) if connection.user_chat_id is not None else None
    if owner_chat_id and trigger_message.chat_id == owner_chat_id and trigger_message.sender_id != owner_chat_id:
        return trigger_message.sender_id
    return trigger_message.chat_id


def _account_send_enabled(*, settings: Settings, account_alias: str) -> bool:
    if not settings.gate_send_enabled:
        return False
    if account_alias == "telegram_mark_ru":
        return settings.gate_account_ru_send_enabled
    if account_alias == "telegram_mark_by":
        return settings.gate_account_by_send_enabled
    return True


def _send_result_text(*, settings: Settings, scope_label: str, status: str, detail: str) -> str:
    return "\n".join(
        [
            _commit_header(settings),
            "",
            "Draft send",
            f"scope: {scope_label}",
            f"status: {status}",
            f"detail: {detail}",
            "next: /inbox",
        ]
    )


def _send_draft_ref(text: str | None) -> str | None:
    normalized = (text or "").strip().casefold()
    for prefix in ("/send_", "/approve_"):
        if normalized.startswith(prefix):
            return normalized.removeprefix(prefix).strip() or None
    parts = normalized.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in {"/send", "send", "/approve", "approve"}:
        return parts[1].strip() or None
    return None


def _commit_header(settings: Settings) -> str:
    return f"Commit: {(settings.deployment_sha or 'unknown')[:12]}"
