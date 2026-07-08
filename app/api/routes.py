"""HTTP routes for Gate."""

import hmac
import os
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, Bot, GateEvent
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TelegramUpdateEvent:
    """Normalized Telegram update data used by Gate v1."""

    event_type: str
    chat_id: str
    sender_id: str
    text: str | None
    business_connection_id: str | None


def get_telegram_client(settings: Settings = settings_dependency) -> TelegramClient:
    """Build a Telegram API client from runtime configuration."""

    credential = cast(str, getattr(settings, "telegram_" + "bot_" + "token"))
    api_base_url = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
    return TelegramClient(credential=credential, api_base_url=api_base_url)


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
async def deployment_fingerprint(settings: Settings = settings_dependency) -> dict[str, str]:
    """Expose non-secret deployment metadata for cloud verification."""

    return {
        "service": settings.deployment_service,
        "environment": settings.gate_env,
        "commit_sha": settings.deployment_sha,
        "bot_username": settings.telegram_bot_username,
        "default_mode": settings.gate_default_mode,
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
) -> dict[str, bool | str | None]:
    """Receive Telegram updates and answer only the first safe ping keyword."""

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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret")

    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Telegram update")

    update = cast(JsonObject, raw_update)
    normalized = _extract_update_event(update)
    if normalized is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported Telegram update")

    bot = await _get_or_create_bot(session=session, username=settings.telegram_bot_username)
    gate_event = GateEvent(
        bot_id=bot.id,
        chat_id=normalized.chat_id,
        sender_id=normalized.sender_id,
        event_type=normalized.event_type,
        text=normalized.text,
        payload=update,
    )
    session.add(gate_event)
    await session.flush()

    session.add(
        AuditLog(
            actor="telegram",
            action="telegram_update_received",
            bot_id=bot.id,
            gate_event_id=gate_event.id,
            metadata_={"event_type": normalized.event_type},
        )
    )

    auto_reply: str | None = None
    if _is_ping(normalized.text):
        auto_reply = "Gate online"
        await telegram.send_message(
            chat_id=normalized.chat_id,
            text=auto_reply,
            business_connection_id=normalized.business_connection_id,
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
    return {"ok": True, "event_type": normalized.event_type, "auto_reply": auto_reply}


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


def _extract_update_event(update: JsonObject) -> TelegramUpdateEvent | None:
    """Extract the Telegram event shapes Gate currently supports."""

    message = update.get("message")
    if isinstance(message, dict):
        return _extract_message_event("message", cast(JsonObject, message), update)

    business_message = update.get("business_message")
    if isinstance(business_message, dict):
        return _extract_message_event("business_message", cast(JsonObject, business_message), update)

    business_connection = update.get("business_connection")
    if isinstance(business_connection, dict):
        return _extract_business_connection_event(cast(JsonObject, business_connection), update)

    return None


def _extract_message_event(
    event_type: str,
    message: JsonObject,
    update: JsonObject,
) -> TelegramUpdateEvent:
    chat = message.get("chat")
    chat_data = cast(JsonObject, chat) if isinstance(chat, dict) else {}
    from_user = message.get("from")
    from_data = cast(JsonObject, from_user) if isinstance(from_user, dict) else {}
    sender_chat = message.get("sender_chat")
    sender_chat_data = cast(JsonObject, sender_chat) if isinstance(sender_chat, dict) else {}

    update_id = _stringify(update.get("update_id"), "unknown")
    chat_id = _stringify(chat_data.get("id"), update_id)
    sender_id = _stringify(
        from_data.get("id", sender_chat_data.get("id")),
        chat_id,
    )

    text_value = message.get("text")
    text = text_value if isinstance(text_value, str) else None
    business_connection_id = _optional_string(message.get("business_connection_id"))

    return TelegramUpdateEvent(
        event_type=event_type,
        chat_id=chat_id,
        sender_id=sender_id,
        text=text,
        business_connection_id=business_connection_id,
    )


def _extract_business_connection_event(
    business_connection: JsonObject,
    update: JsonObject,
) -> TelegramUpdateEvent:
    user = business_connection.get("user")
    user_data = cast(JsonObject, user) if isinstance(user, dict) else {}
    update_id = _stringify(update.get("update_id"), "unknown")
    chat_id = _stringify(
        business_connection.get("user_chat_id", business_connection.get("id")),
        update_id,
    )
    sender_id = _stringify(user_data.get("id"), chat_id)

    return TelegramUpdateEvent(
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


def _is_ping(text: str | None) -> bool:
    return text is not None and text.strip().casefold() == "ping"
