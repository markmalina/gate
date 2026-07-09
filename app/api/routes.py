"""HTTP routes for Gate."""

import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, Bot, GateEvent, Message, MessageDirection, TelegramUpdate
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)

JsonObject = dict[str, Any]


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


def get_telegram_client(settings: Settings = settings_dependency) -> TelegramClient:
    """Build a Telegram API client from runtime configuration."""

    api_base_url = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
    return TelegramClient(credential=settings.telegram_bot_token, api_base_url=api_base_url)


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

    _require_admin_bearer(authorization=authorization, settings=settings)

    result = await session.execute(
        select(GateEvent)
        .order_by(GateEvent.received_at.desc(), GateEvent.id.desc())
        .limit(limit)
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

    _require_admin_bearer(authorization=authorization, settings=settings)

    result = await session.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
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

    _require_admin_bearer(authorization=authorization, settings=settings)

    event_count_result = await session.execute(select(func.count(GateEvent.id)))
    latest_event_result = await session.execute(select(func.max(GateEvent.received_at)))
    audit_count_result = await session.execute(select(func.count(AuditLog.id)))
    latest_audit_result = await session.execute(select(func.max(AuditLog.created_at)))
    message_count_result = await session.execute(select(func.count(Message.id)))
    latest_message_result = await session.execute(select(func.max(Message.created_at)))

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
    }


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
    await session.commit()

    auto_reply: str | None = None
    send_status = "not_applicable"
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
                sent_at=datetime.now(UTC),
            )
        )
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


def _require_admin_bearer(*, authorization: str | None, settings: Settings) -> None:
    """Require the configured admin bearer token for read-only admin routes."""

    if not settings.admin_bearer_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin bearer token is not configured",
        )

    scheme, _, token = (authorization or "").partition(" ")
    invalid_token = (
        scheme.casefold() != "bearer"
        or not token
        or not hmac.compare_digest(token, settings.admin_bearer_token)
    )
    if invalid_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin bearer token",
        )


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

    text_value = message.get("text")
    text = text_value if isinstance(text_value, str) else None
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
