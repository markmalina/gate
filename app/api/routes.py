"""HTTP routes for Gate."""

import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast as sql_cast

from app.api.admin_auth import require_admin_bearer
from app.api.deps import get_db_session
from app.application.telegram_message_content import extract_telegram_message_text
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

JsonObject = dict[str, Any]

DRAFT_SOURCE = "gate-local-draft-v1"
TEST_PROBE_TEXT = "test guided draft"


@dataclass(frozen=True)
class TelegramUpdateEvent:
    """Normalized Telegram update data used by Gate v1."""

    update_id: int
    message_id: int | None
    event_type: str
    chat_id: str
    sender_id: str
    text: str | None
    business_connection_id: str | None


@dataclass(frozen=True)
class TelegramAdminCommand:
    """Parsed Telegram admin console command."""

    name: str
    task_ref: str | None = None


def get_telegram_client(request: Request) -> TelegramClient:
    """Return the process-owned Telegram client installed by application lifespan."""

    telegram = getattr(request.app.state, "telegram_client", None)
    if not isinstance(telegram, TelegramClient):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram client runtime is not initialized",
        )
    return telegram


telegram_client_dependency = Depends(get_telegram_client)


@router.get("/health")
async def health(settings: Settings = settings_dependency) -> dict[str, str]:
    """Return a lightweight health response suitable for Railway checks."""

    return {
        "status": "ok",
        "service": "gate",
        "environment": settings.gate_env,
        "default_mode": settings.gate_default_mode,
    }


@router.get("/deployment/fingerprint")
async def deployment_fingerprint(
    settings: Settings = settings_dependency,
) -> dict[str, str]:
    """Expose non-secret deployment metadata for cloud verification."""

    return {
        "service": settings.deployment_service,
        "environment": settings.gate_env,
        "commit_sha": settings.deployment_sha,
        "bot_username": settings.telegram_bot_username,
        "default_mode": settings.gate_default_mode,
    }


@router.get("/admin/events/recent")
async def admin_recent_events(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> dict[str, list[JsonObject]]:
    """Return recent Gate events for operational inspection."""

    require_admin_bearer(authorization=authorization, settings=settings)

    result = await session.execute(
        select(GateEvent).order_by(GateEvent.received_at.desc(), GateEvent.id.desc()).limit(limit)
    )
    events = result.scalars().all()
    return {"events": [_serialize_gate_event(event) for event in events]}


@router.get("/admin/audit/recent")
async def admin_recent_audit_logs(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> dict[str, list[JsonObject]]:
    """Return recent audit records for operational inspection."""

    require_admin_bearer(authorization=authorization, settings=settings)

    result = await session.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    )
    audit_logs = result.scalars().all()
    return {"audit_logs": [_serialize_audit_log(audit_log) for audit_log in audit_logs]}


@router.get("/admin/status")
async def admin_status(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> JsonObject:
    """Return a compact read-only operational status snapshot for Gate."""

    require_admin_bearer(authorization=authorization, settings=settings)
    return await _build_admin_status_payload(session=session, settings=settings)


@router.get("/admin/inbox/unanswered")
async def admin_unanswered_inbox(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> dict[str, list[JsonObject]]:
    """Return active reply tasks with sanitized conversation and message context."""

    require_admin_bearer(authorization=authorization, settings=settings)
    return {"reply_tasks": await _load_unanswered_reply_tasks(session=session, limit=limit)}


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Receive Telegram updates, persist them durably and answer safe ping only."""

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

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram update",
        )

    update = cast(JsonObject, raw_update)
    normalized = _extract_update_event(update)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported Telegram update",
        )

    bot = await _get_or_create_bot(
        session=session,
        username=settings.telegram_bot_username,
    )

    existing_update = await _find_telegram_update(
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

    now = datetime.now(UTC)
    conversation = await _get_or_create_conversation(
        session=session,
        bot_id=bot.id,
        account_id=None,
        project_id=None,
        chat_id=normalized.chat_id,
        business_connection_id=normalized.business_connection_id,
        title=None,
        happened_at=now,
    )

    inbound_message = Message(
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
        received_at=now,
    )
    session.add(inbound_message)
    await session.flush()

    admin_command = _parse_admin_console_command(normalized.text)
    if _is_human_text_message(normalized) and admin_command is None:
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
        reply_task = ReplyTask(
            conversation_id=conversation.id,
            trigger_message_id=inbound_message.id,
            status=ReplyTaskStatus.NEEDS_REPLY.value,
            reason="inbound_human_text",
        )
        session.add(reply_task)
        await session.flush()
        if _is_test_probe_text(normalized.text) and _is_telegram_admin_sender(
            normalized.sender_id
        ):
            await _update_latest_test_guide_message(
                session=session,
                telegram=telegram,
                settings=settings,
                chat_id=normalized.chat_id,
                business_connection_id=normalized.business_connection_id,
                task_ref=str(reply_task.id)[:8],
                bot_id=bot.id,
                gate_event_id=gate_event.id,
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
    await session.commit()

    auto_reply: str | None = None
    send_status = "not_applicable"
    if admin_command is not None:
        if _is_telegram_admin_sender(normalized.sender_id):
            auto_reply = await _build_telegram_admin_reply(
                command=admin_command,
                session=session,
                settings=settings,
            )
            audit_action = f"telegram_admin_console_{admin_command.name}_sent"
        else:
            auto_reply = "Gate online"
            audit_action = "telegram_admin_console_rejected"

        try:
            send_result = await telegram.send_message(
                chat_id=normalized.chat_id,
                text=auto_reply,
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
                        "command": admin_command.name,
                        "task_ref": admin_command.task_ref,
                        "error_type": exc.__class__.__name__,
                    },
                )
            )
            await session.commit()
            return {
                "ok": True,
                "event_type": normalized.event_type,
                "auto_reply": auto_reply,
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
                text=auto_reply,
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
                metadata_={"command": admin_command.name, "task_ref": admin_command.task_ref},
            )
        )
        await session.commit()
        send_status = "sent"
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": auto_reply,
            "duplicate": False,
            "send_status": send_status,
        }

    if _is_ping(normalized.text):
        auto_reply = "Gate online"
        try:
            send_result = await telegram.send_message(
                chat_id=normalized.chat_id,
                text=auto_reply,
                business_connection_id=normalized.business_connection_id,
            )
        except Exception as exc:
            session.add(
                AuditLog(
                    actor="gate",
                    action="auto_safe_reply_failed",
                    bot_id=bot.id,
                    gate_event_id=gate_event.id,
                    metadata_={
                        "reply": auto_reply,
                        "rule": "ping",
                        "error_type": exc.__class__.__name__,
                    },
                )
            )
            await session.commit()
            return {
                "ok": True,
                "event_type": normalized.event_type,
                "auto_reply": auto_reply,
                "duplicate": False,
                "send_status": "failed",
            }

        outbound_at = datetime.now(UTC)
        session.add(
            Message(
                bot_id=bot.id,
                gate_event_id=gate_event.id,
                direction=MessageDirection.OUTBOUND.value,
                source="gate",
                event_type="auto_safe_reply",
                chat_id=normalized.chat_id,
                sender_id=settings.telegram_bot_username,
                recipient_id=normalized.chat_id,
                text=auto_reply,
                business_connection_id=normalized.business_connection_id,
                payload=send_result,
                sent_at=outbound_at,
            )
        )
        conversation.status = ConversationStatus.REPLIED.value
        conversation.requires_reply = False
        conversation.last_outbound_at = outbound_at
        conversation.last_message_at = outbound_at
        await _satisfy_reply_tasks(session=session, conversation_id=conversation.id)
        session.add(
            AuditLog(
                actor="gate",
                action="auto_safe_reply_sent",
                bot_id=bot.id,
                gate_event_id=gate_event.id,
                metadata_={"reply": auto_reply, "rule": "ping"},
            )
        )
        await session.commit()
        send_status = "sent"

    return {
        "ok": True,
        "event_type": normalized.event_type,
        "auto_reply": auto_reply,
        "duplicate": False,
        "send_status": send_status,
    }


async def _build_admin_status_payload(*, session: AsyncSession, settings: Settings) -> JsonObject:
    event_count_result = await session.execute(select(func.count(GateEvent.id)))
    latest_event_result = await session.execute(select(func.max(GateEvent.received_at)))
    audit_count_result = await session.execute(select(func.count(AuditLog.id)))
    latest_audit_result = await session.execute(select(func.max(AuditLog.created_at)))
    message_count_result = await session.execute(select(func.count(Message.id)))
    latest_message_result = await session.execute(select(func.max(Message.created_at)))
    reply_task_count_result = await session.execute(select(func.count(ReplyTask.id)))
    unanswered_count_result = await session.execute(
        select(func.count(ReplyTask.id)).where(
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value
        )
    )

    latest_event_received_at = cast(
        datetime | None,
        latest_event_result.scalar_one(),
    )
    latest_audit_created_at = cast(
        datetime | None,
        latest_audit_result.scalar_one(),
    )
    latest_message_created_at = cast(
        datetime | None,
        latest_message_result.scalar_one(),
    )

    return {
        "service": {
            "status": "ok",
            "name": "gate",
            "environment": settings.gate_env,
            "default_mode": settings.gate_default_mode,
        },
        "deployment": {
            "service": settings.deployment_service,
            "commit_sha": settings.deployment_sha,
            "bot_username": settings.telegram_bot_username,
        },
        "events": {
            "count": _serialize_count(event_count_result.scalar_one()),
            "last_received_at": _serialize_datetime(latest_event_received_at),
        },
        "audit_logs": {
            "count": _serialize_count(audit_count_result.scalar_one()),
            "last_created_at": _serialize_datetime(latest_audit_created_at),
        },
        "messages": {
            "count": _serialize_count(message_count_result.scalar_one()),
            "last_created_at": _serialize_datetime(latest_message_created_at),
        },
        "reply_tasks": {
            "count": _serialize_count(reply_task_count_result.scalar_one()),
            "unanswered": _serialize_count(unanswered_count_result.scalar_one()),
        },
    }


async def _load_unanswered_reply_tasks(*, session: AsyncSession, limit: int) -> list[JsonObject]:
    result = await session.execute(
        select(ReplyTask, Conversation, Message)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value)
        .order_by(ReplyTask.priority.asc(), ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(limit)
    )
    return [
        _serialize_reply_task(task, conversation, message)
        for task, conversation, message in result.all()
    ]


async def _build_telegram_admin_reply(
    *,
    command: TelegramAdminCommand,
    session: AsyncSession,
    settings: Settings,
) -> str:
    if command.name == "status":
        status_payload = await _build_admin_status_payload(session=session, settings=settings)
        return _format_telegram_admin_status(status_payload)

    if command.name == "inbox":
        reply_tasks = await _load_unanswered_reply_tasks(session=session, limit=10)
        return _format_telegram_unanswered_inbox(reply_tasks)

    if command.name == "test":
        return _format_telegram_test_guide(
            commit_sha=settings.deployment_sha,
            bot_username=settings.telegram_bot_username,
        )

    if command.name == "drafts":
        return await _list_reply_drafts(command=command, session=session)

    if command.name == "draft":
        return await _create_reply_draft(command=command, session=session)

    return await _apply_reply_task_action(command=command, session=session)


async def _create_reply_draft(
    *, command: TelegramAdminCommand, session: AsyncSession
) -> str:
    rows_or_error = await _load_single_reply_task_row(command=command, session=session)
    if isinstance(rows_or_error, str):
        return rows_or_error

    task, conversation, message = rows_or_error
    draft_text = _build_reply_draft_text(message.text)
    prompt_context: JsonObject = {
        "bounded_context": True,
        "source_message": {
            "id": str(message.id),
            "chat_id": message.chat_id,
            "text": _truncate_text(message.text or "", 600),
            "created_at": _serialize_datetime(message.created_at),
            "received_at": _serialize_datetime(message.received_at),
        },
        "reply_task": {
            "id": str(task.id),
            "status": task.status,
            "reason": task.reason,
        },
        "conversation": {
            "id": str(conversation.id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
        },
    }
    draft = ReplyDraft(
        reply_task_id=task.id,
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        status=ReplyDraftStatus.GENERATED.value,
        source=DRAFT_SOURCE,
        draft_text=draft_text,
        prompt_context=prompt_context,
    )
    session.add(draft)
    await session.flush()
    session.add(
        AuditLog(
            actor="gate",
            action="reply_draft_created_from_telegram",
            bot_id=conversation.bot_id,
            gate_event_id=None,
            metadata_={
                "task_id": str(task.id),
                "draft_id": str(draft.id),
                "source": DRAFT_SOURCE,
            },
        )
    )
    return _format_reply_draft_result(draft=draft, task=task)


async def _list_reply_drafts(*, command: TelegramAdminCommand, session: AsyncSession) -> str:
    rows_or_error = await _load_single_reply_task_row(command=command, session=session)
    if isinstance(rows_or_error, str):
        return rows_or_error

    task, _conversation, _message = rows_or_error
    result = await session.execute(
        select(ReplyDraft)
        .where(ReplyDraft.reply_task_id == task.id)
        .order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .limit(5)
    )
    drafts = list(result.scalars().all())
    return _format_reply_drafts_result(task=task, drafts=drafts)


async def _apply_reply_task_action(
    *, command: TelegramAdminCommand, session: AsyncSession
) -> str:
    rows_or_error = await _load_single_reply_task_row(command=command, session=session)
    if isinstance(rows_or_error, str):
        return rows_or_error

    task, conversation, _message = rows_or_error
    old_status = task.status
    action_statuses = {
        "done": ReplyTaskStatus.REPLIED.value,
        "dismiss": ReplyTaskStatus.DISMISSED.value,
        "snooze": ReplyTaskStatus.SNOOZED.value,
        "reopen": ReplyTaskStatus.NEEDS_REPLY.value,
    }
    task.status = action_statuses[command.name]
    task.due_at = None

    if command.name == "reopen":
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
    elif command.name == "done":
        conversation.status = ConversationStatus.REPLIED.value
        conversation.requires_reply = False
    elif command.name == "dismiss":
        conversation.status = ConversationStatus.CLOSED.value
        conversation.requires_reply = False
    else:
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = False

    session.add(
        AuditLog(
            actor="gate",
            action=f"reply_task_{command.name}_from_telegram",
            bot_id=conversation.bot_id,
            gate_event_id=None,
            metadata_={
                "task_id": str(task.id),
                "task_ref": command.task_ref,
                "from_status": old_status,
                "to_status": task.status,
            },
        )
    )
    return _format_reply_task_action_result(command=command, task=task)


async def _load_single_reply_task_row(
    *, command: TelegramAdminCommand, session: AsyncSession
) -> tuple[ReplyTask, Conversation, Message] | str:
    task_ref = (command.task_ref or "").strip()
    if not task_ref:
        return f"Usage: /{command.name} <task>"
    if len(task_ref) < 4:
        return "Task reference is too short. Use the task id from /inbox."

    rows = await _find_reply_task_rows_by_ref(session=session, task_ref=task_ref)
    if not rows:
        return f"Task not found: {task_ref}"
    if len(rows) > 1:
        return f"Task reference is ambiguous: {task_ref}"
    return rows[0]


async def _find_reply_task_rows_by_ref(
    *, session: AsyncSession, task_ref: str
) -> list[tuple[ReplyTask, Conversation, Message]]:
    result = await session.execute(
        select(ReplyTask, Conversation, Message)
        .join(Conversation, ReplyTask.conversation_id == Conversation.id)
        .join(Message, ReplyTask.trigger_message_id == Message.id)
        .where(sql_cast(ReplyTask.id, String).like(f"{task_ref}%"))
        .order_by(ReplyTask.created_at.desc(), ReplyTask.id.desc())
        .limit(2)
    )
    return [(task, conversation, message) for task, conversation, message in result.all()]


async def _update_latest_test_guide_message(
    *,
    session: AsyncSession,
    telegram: TelegramClient,
    settings: Settings,
    chat_id: str,
    business_connection_id: str | None,
    task_ref: str,
    bot_id: Any,
    gate_event_id: Any,
) -> None:
    guide_message = await _find_latest_test_guide_message(
        session=session,
        chat_id=chat_id,
        business_connection_id=business_connection_id,
    )
    if guide_message is None:
        return

    message_id = _extract_telegram_message_id(guide_message.payload)
    if message_id is None:
        return

    updated_text = _format_telegram_test_guide(
        commit_sha=settings.deployment_sha,
        bot_username=settings.telegram_bot_username,
        task_ref=task_ref,
    )
    try:
        edit_result = await telegram.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=updated_text,
            business_connection_id=business_connection_id,
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_test_guide_update_failed",
                bot_id=bot_id,
                gate_event_id=gate_event_id,
                metadata_={
                    "task_ref": task_ref,
                    "error_type": exc.__class__.__name__,
                },
            )
        )
        return

    guide_message.text = updated_text
    guide_message.payload = {
        "original_send": guide_message.payload,
        "latest_edit": edit_result,
    }
    session.add(
        AuditLog(
            actor="gate",
            action="telegram_test_guide_updated",
            bot_id=bot_id,
            gate_event_id=gate_event_id,
            metadata_={"task_ref": task_ref, "message_id": message_id},
        )
    )


async def _find_latest_test_guide_message(
    *,
    session: AsyncSession,
    chat_id: str,
    business_connection_id: str | None,
) -> Message | None:
    business_filter = (
        Message.business_connection_id.is_(None)
        if business_connection_id is None
        else Message.business_connection_id == business_connection_id
    )
    result = await session.execute(
        select(Message)
        .where(
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.source == "gate",
            Message.event_type == "telegram_admin_console_reply",
            Message.chat_id == chat_id,
            business_filter,
            Message.text.like("Gate test: draft flow%"),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return cast(Message | None, result.scalar_one_or_none())


def _extract_telegram_message_id(payload: JsonObject | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    return _optional_int(result.get("message_id"))


def _build_reply_draft_text(message_text: str | None) -> str:
    text = _truncate_text((message_text or "").strip(), 360)
    if not text:
        return "Привет. Вижу сообщение. Вернусь с ответом чуть позже."
    return "\n".join(
        [
            "Привет.",
            f"Вижу твоё сообщение: «{text}».",
            "Вернусь с ответом чуть позже.",
        ]
    )


def _format_reply_draft_result(*, draft: ReplyDraft, task: ReplyTask) -> str:
    return "\n".join(
        [
            f"Draft created: {str(draft.id)[:8]}",
            f"task: {str(task.id)[:8]}",
            "",
            draft.draft_text,
            "",
            "Not sent.",
        ]
    )


def _format_reply_drafts_result(*, task: ReplyTask, drafts: list[ReplyDraft]) -> str:
    task_ref = str(task.id)[:8]
    if not drafts:
        return "\n".join(
            [
                f"No drafts for task: {task_ref}",
                f"Press /draft_{task_ref} to create one.",
            ]
        )

    lines = [f"Drafts for task: {task_ref}"]
    for index, draft in enumerate(drafts, start=1):
        lines.extend(
            [
                "",
                f"{index}. {str(draft.id)[:8]}",
                f"created: {_serialize_datetime(draft.created_at)}",
                _truncate_text(draft.draft_text, 700),
                "",
                "Not sent.",
            ]
        )
    return "\n".join(lines)


def _format_reply_task_action_result(
    *, command: TelegramAdminCommand, task: ReplyTask
) -> str:
    labels = {
        "done": "Marked replied",
        "dismiss": "Dismissed",
        "snooze": "Snoozed",
        "reopen": "Reopened",
    }
    return f"{labels[command.name]}: {str(task.id)[:8]}"


def _format_telegram_admin_status(status_payload: JsonObject) -> str:
    service = cast(JsonObject, status_payload["service"])
    deployment = cast(JsonObject, status_payload["deployment"])
    messages = cast(JsonObject, status_payload["messages"])
    reply_tasks = cast(JsonObject, status_payload["reply_tasks"])
    commit_sha = str(deployment["commit_sha"])
    short_sha = commit_sha[:12] if commit_sha != "unknown" else commit_sha
    return "\n".join(
        [
            "Gate status",
            f"Environment: {service['environment']}",
            f"Commit: {short_sha}",
            f"Bot: {deployment['bot_username']}",
            f"Messages: {messages['count']}",
            f"Reply tasks: {reply_tasks['count']}",
            f"Unanswered: {reply_tasks['unanswered']}",
        ]
    )


def _format_telegram_test_guide(
    *, commit_sha: str, bot_username: str, task_ref: str | None = None
) -> str:
    short_sha = commit_sha[:12] if commit_sha != "unknown" else commit_sha
    task_label = task_ref or "<pending>"
    command_ref = task_ref or "<task>"
    return "\n".join(
        [
            "Gate test: draft flow",
            f"Commit: {short_sha}",
            f"Bot: {bot_username}",
            f"Task: {task_label}",
            "",
            "1. Send text message:",
            TEST_PROBE_TEXT,
            "Expected: this message is edited with the real task id.",
            "",
            "2. Press /inbox",
            "Expected: task appears with clickable actions.",
            "",
            f"3. Press /draft_{command_ref} from /inbox",
            "Expected: Draft created ... Not sent.",
            "",
            f"4. Press /drafts_{command_ref}",
            "Expected: saved draft history appears.",
            "",
            "5. Press /inbox",
            "Expected: task still appears.",
            "",
            f"6. Press /done_{command_ref}",
            "Expected: Marked replied.",
            "",
            "7. Press /inbox",
            "Expected: No unanswered messages.",
        ]
    )


def _format_telegram_unanswered_inbox(reply_tasks: list[JsonObject]) -> str:
    if not reply_tasks:
        return "No unanswered messages."

    lines = ["Unanswered inbox:"]
    for index, task in enumerate(reply_tasks, start=1):
        conversation = cast(JsonObject, task["conversation"])
        message = cast(JsonObject, task["message"])
        task_ref = str(task["id"])[:8]
        text = str(message.get("text") or "")
        if len(text) > 160:
            text = f"{text[:157]}..."
        lines.extend(
            [
                "",
                f"{index}. {text}",
                f"chat: {conversation['chat_id']}",
                f"task: {task_ref}",
                f"created: {task['created_at']}",
                (
                    f"actions: /draft_{task_ref} | /drafts_{task_ref} | "
                    f"/done_{task_ref} | /dismiss_{task_ref} | /snooze_{task_ref}"
                ),
            ]
        )
    return "\n".join(lines)


def _parse_admin_console_command(text: str | None) -> TelegramAdminCommand | None:
    normalized = (text or "").strip().casefold()
    if normalized in {"/status", "status", "статус"}:
        return TelegramAdminCommand(name="status")
    if normalized in {"/inbox", "inbox", "где я не ответил"}:
        return TelegramAdminCommand(name="inbox")
    if normalized in {"/test", "test"}:
        return TelegramAdminCommand(name="test")

    action_aliases = {
        "/draft": "draft",
        "draft": "draft",
        "/drafts": "drafts",
        "drafts": "drafts",
        "/done": "done",
        "done": "done",
        "/dismiss": "dismiss",
        "dismiss": "dismiss",
        "/snooze": "snooze",
        "snooze": "snooze",
        "/reopen": "reopen",
        "reopen": "reopen",
    }
    action = action_aliases.get(normalized)
    if action is not None:
        return TelegramAdminCommand(name=action)

    underscore_action_aliases = {
        "/drafts_": "drafts",
        "/draft_": "draft",
        "/done_": "done",
        "/dismiss_": "dismiss",
        "/snooze_": "snooze",
        "/reopen_": "reopen",
    }
    for prefix, underscore_action in underscore_action_aliases.items():
        if normalized.startswith(prefix):
            return TelegramAdminCommand(
                name=underscore_action,
                task_ref=normalized.removeprefix(prefix),
            )

    parts = normalized.split(maxsplit=1)
    if len(parts) != 2:
        return None

    action = action_aliases.get(parts[0])
    if action is None:
        return None
    return TelegramAdminCommand(name=action, task_ref=parts[1])


def _is_telegram_admin_sender(sender_id: str) -> bool:
    configured = os.getenv("TELEGRAM_ADMIN_USER_IDS", "")
    normalized_config = configured.replace(";", ",").replace(" ", ",")
    admin_ids = {item.strip() for item in normalized_config.split(",") if item.strip()}
    return sender_id in admin_ids


def _is_test_probe_text(text: str | None) -> bool:
    return text is not None and text.strip().casefold() == TEST_PROBE_TEXT


async def _get_or_create_conversation(
    *,
    session: AsyncSession,
    bot_id: Any,
    account_id: Any | None,
    project_id: Any | None,
    chat_id: str,
    business_connection_id: str | None,
    title: str | None,
    happened_at: datetime,
) -> Conversation:
    """Return and update the conversation for an inbound Telegram message."""

    account_filter = (
        Conversation.account_id.is_(None)
        if account_id is None
        else Conversation.account_id == account_id
    )
    project_filter = (
        Conversation.project_id.is_(None)
        if project_id is None
        else Conversation.project_id == project_id
    )
    business_connection_filter = (
        Conversation.business_connection_id.is_(None)
        if business_connection_id is None
        else Conversation.business_connection_id == business_connection_id
    )

    result = await session.execute(
        select(Conversation).where(
            Conversation.bot_id == bot_id,
            account_filter,
            project_filter,
            Conversation.chat_id == chat_id,
            business_connection_filter,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(
            bot_id=bot_id,
            account_id=account_id,
            project_id=project_id,
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            title=title,
            status=ConversationStatus.OPEN.value,
            requires_reply=False,
            last_message_at=happened_at,
            last_inbound_at=happened_at,
        )
        session.add(conversation)
        await session.flush()
        return conversation

    conversation.last_message_at = happened_at
    conversation.last_inbound_at = happened_at
    if title and conversation.title is None:
        conversation.title = title
    return conversation


async def _satisfy_reply_tasks(*, session: AsyncSession, conversation_id: Any) -> None:
    """Mark open reply tasks for a conversation as replied."""

    result = await session.execute(
        select(ReplyTask).where(
            ReplyTask.conversation_id == conversation_id,
            ReplyTask.status == ReplyTaskStatus.NEEDS_REPLY.value,
        )
    )
    for task in result.scalars().all():
        task.status = ReplyTaskStatus.REPLIED.value


def _is_human_text_message(normalized: TelegramUpdateEvent) -> bool:
    """Return true when a Telegram update is an inbound human text message."""

    return normalized.event_type in {"message", "business_message"} and bool(normalized.text)


async def _get_or_create_bot(*, session: AsyncSession, username: str) -> Bot:
    """Return the configured bot row, creating it when the database is empty."""

    result = await session.execute(select(Bot).where(Bot.username == username))
    bot = result.scalar_one_or_none()
    if bot is not None:
        return bot

    bot = Bot(username=username, token_secret_ref="runtime_env", is_active=True)
    session.add(bot)
    await session.flush()
    return bot


async def _find_telegram_update(
    *,
    session: AsyncSession,
    bot_id: Any,
    update_id: int,
) -> TelegramUpdate | None:
    """Return an existing Telegram update receipt for webhook idempotency."""

    result = await session.execute(
        select(TelegramUpdate).where(
            TelegramUpdate.bot_id == bot_id,
            TelegramUpdate.update_id == update_id,
        )
    )
    return result.scalar_one_or_none()



def _serialize_reply_task(
    task: ReplyTask, conversation: Conversation, message: Message
) -> JsonObject:
    """Serialize a reply task without raw Telegram payloads or secret-bearing fields."""

    return {
        "id": str(task.id),
        "status": task.status,
        "priority": task.priority,
        "reason": task.reason,
        "due_at": _serialize_datetime(task.due_at),
        "created_at": _serialize_datetime(task.created_at),
        "updated_at": _serialize_datetime(task.updated_at),
        "conversation": {
            "id": str(conversation.id),
            "bot_id": str(conversation.bot_id),
            "account_id": _optional_string(conversation.account_id),
            "project_id": _optional_string(conversation.project_id),
            "chat_id": conversation.chat_id,
            "business_connection_id": conversation.business_connection_id,
            "title": conversation.title,
            "status": conversation.status,
            "requires_reply": conversation.requires_reply,
            "last_message_at": _serialize_datetime(conversation.last_message_at),
            "last_inbound_at": _serialize_datetime(conversation.last_inbound_at),
            "last_outbound_at": _serialize_datetime(conversation.last_outbound_at),
        },
        "message": {
            "id": str(message.id),
            "direction": message.direction,
            "source": message.source,
            "event_type": message.event_type,
            "chat_id": message.chat_id,
            "sender_id": message.sender_id,
            "text": message.text,
            "telegram_update_id": message.telegram_update_id,
            "telegram_message_id": message.telegram_message_id,
            "business_connection_id": message.business_connection_id,
            "received_at": _serialize_datetime(message.received_at),
            "created_at": _serialize_datetime(message.created_at),
        },
    }


def _serialize_gate_event(event: GateEvent) -> JsonObject:
    """Serialize a Gate event without exposing raw Telegram payloads."""

    return {
        "id": str(event.id),
        "bot_id": str(event.bot_id),
        "account_id": _optional_string(event.account_id),
        "project_id": _optional_string(event.project_id),
        "chat_id": event.chat_id,
        "sender_id": event.sender_id,
        "event_type": event.event_type,
        "text": event.text,
        "telegram_update_id": event.telegram_update_id,
        "telegram_message_id": event.telegram_message_id,
        "business_connection_id": event.business_connection_id,
        "received_at": _serialize_datetime(event.received_at),
    }


def _serialize_audit_log(audit_log: AuditLog) -> JsonObject:
    """Serialize an audit log row for read-only inspection."""

    return {
        "id": str(audit_log.id),
        "actor": audit_log.actor,
        "action": audit_log.action,
        "bot_id": _optional_string(audit_log.bot_id),
        "account_id": _optional_string(audit_log.account_id),
        "project_id": _optional_string(audit_log.project_id),
        "gate_event_id": _optional_string(audit_log.gate_event_id),
        "metadata": audit_log.metadata_,
        "created_at": _serialize_datetime(audit_log.created_at),
    }


def _serialize_datetime(value: datetime | None) -> str | None:
    """Serialize datetimes for JSON responses."""

    if value is None:
        return None
    return value.isoformat()


def _serialize_count(value: Any) -> int:
    """Serialize SQL aggregate counts into plain JSON integers."""

    if value is None:
        return 0
    return int(value)


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _extract_update_event(update: JsonObject) -> TelegramUpdateEvent | None:
    """Extract the Telegram event shapes Gate currently supports."""

    update_id = _optional_int(update.get("update_id"))
    if update_id is None:
        return None

    message = update.get("message")
    if isinstance(message, dict):
        return _extract_message_event(update_id, "message", cast(JsonObject, message))

    business_message = update.get("business_message")
    if isinstance(business_message, dict):
        return _extract_message_event(
            update_id,
            "business_message",
            cast(JsonObject, business_message),
        )

    business_connection = update.get("business_connection")
    if isinstance(business_connection, dict):
        return _extract_business_connection_event(
            update_id,
            cast(JsonObject, business_connection),
        )

    return None


def _extract_message_event(
    update_id: int,
    event_type: str,
    message: JsonObject,
) -> TelegramUpdateEvent:
    chat = message.get("chat")
    chat_data = cast(JsonObject, chat) if isinstance(chat, dict) else {}
    from_user = message.get("from")
    from_data = cast(JsonObject, from_user) if isinstance(from_user, dict) else {}
    sender_chat = message.get("sender_chat")
    sender_chat_data = cast(JsonObject, sender_chat) if isinstance(sender_chat, dict) else {}

    chat_id = _stringify(chat_data.get("id"), str(update_id))
    sender_id = _stringify(
        from_data.get("id", sender_chat_data.get("id")),
        chat_id,
    )

    text = extract_telegram_message_text(message)
    business_connection_id = _optional_string(message.get("business_connection_id"))

    return TelegramUpdateEvent(
        update_id=update_id,
        message_id=_optional_int(message.get("message_id")),
        event_type=event_type,
        chat_id=chat_id,
        sender_id=sender_id,
        text=text,
        business_connection_id=business_connection_id,
    )


def _extract_business_connection_event(
    update_id: int,
    business_connection: JsonObject,
) -> TelegramUpdateEvent:
    user = business_connection.get("user")
    user_data = cast(JsonObject, user) if isinstance(user, dict) else {}
    chat_id = _stringify(
        business_connection.get("user_chat_id", business_connection.get("id")),
        str(update_id),
    )
    sender_id = _stringify(user_data.get("id"), chat_id)

    return TelegramUpdateEvent(
        update_id=update_id,
        message_id=None,
        event_type="business_connection",
        chat_id=chat_id,
        sender_id=sender_id,
        text=None,
        business_connection_id=_optional_string(business_connection.get("id")),
    )


def _stringify(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    return str(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _is_ping(text: str | None) -> bool:
    return text is not None and text.strip().casefold() == "ping"
