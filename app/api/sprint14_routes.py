"""Sprint 14 Telegram Business connection routing layer."""

import hmac
import os
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint11b_routes as sprint11b
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import Settings, get_settings
from app.models.domain import (
    DEFAULT_PROJECT_ID,
    TELEGRAM_MARK_BY_ACCOUNT_ID,
    TELEGRAM_MARK_RU_ACCOUNT_ID,
    Account,
    AuditLog,
    ConversationStatus,
    GateEvent,
    Message,
    MessageDirection,
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
async def sprint14_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Persist Telegram Business updates with account-aware context."""

    update = await _read_update(request)
    if not _is_business_update(update):
        return await sprint11b.sprint11b_telegram_webhook(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    _require_telegram_secret(x_telegram_secret=x_telegram_secret)
    normalized = legacy._extract_update_event(update)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported Telegram update",
        )

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
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": None,
            "duplicate": True,
            "send_status": "duplicate",
        }

    if normalized.event_type == "business_connection":
        account_id = await _resolve_business_connection_account_id(
            session=session,
            update=update,
            business_connection_id=normalized.business_connection_id,
            settings=settings,
        )
        received_at = datetime.now(UTC)
        await _upsert_business_connection(
            session=session,
            account_id=account_id,
            update=update,
            received_at=received_at,
        )
        gate_event = GateEvent(
            bot_id=bot.id,
            account_id=account_id,
            project_id=DEFAULT_PROJECT_ID,
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
        await session.flush()
        session.add(
            Message(
                bot_id=bot.id,
                account_id=account_id,
                project_id=DEFAULT_PROJECT_ID,
                gate_event_id=gate_event.id,
                direction=MessageDirection.INBOUND.value,
                source="telegram",
                event_type=normalized.event_type,
                chat_id=normalized.chat_id,
                sender_id=normalized.sender_id,
                recipient_id=None,
                text=normalized.text,
                telegram_update_id=normalized.update_id,
                telegram_message_id=normalized.message_id,
                business_connection_id=normalized.business_connection_id,
                payload={"raw_update_ref": "telegram_updates"},
                received_at=received_at,
                sent_at=None,
            )
        )
        session.add(
            TelegramUpdate(
                bot_id=bot.id,
                update_id=normalized.update_id,
                payload=update,
                processing_status="processed",
                processed_at=received_at,
            )
        )
        session.add(
            AuditLog(
                actor="telegram",
                action="telegram_business_connection_synchronized",
                bot_id=bot.id,
                account_id=account_id,
                project_id=DEFAULT_PROJECT_ID,
                gate_event_id=gate_event.id,
                metadata_={
                    "telegram_update_id": normalized.update_id,
                    "business_connection_id": normalized.business_connection_id,
                },
            )
        )
        await session.commit()
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": None,
            "duplicate": False,
            "send_status": "connection_synchronized",
        }

    account_id = await _resolve_account_id(
        session=session,
        business_connection_id=normalized.business_connection_id,
        settings=settings,
    )
    owner_identity_ids = await _find_owner_identity_ids(
        session=session,
        account_id=account_id,
    )
    is_outbound = _is_outbound_business_message(
        sender_id=normalized.sender_id,
        owner_identity_ids=owner_identity_ids,
    )
    message_direction = (
        MessageDirection.OUTBOUND.value if is_outbound else MessageDirection.INBOUND.value
    )
    gate_event = GateEvent(
        bot_id=bot.id,
        account_id=account_id,
        project_id=DEFAULT_PROJECT_ID,
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
    conversation = await legacy._get_or_create_conversation(
        session=session,
        bot_id=bot.id,
        account_id=account_id,
        project_id=DEFAULT_PROJECT_ID,
        chat_id=normalized.chat_id,
        business_connection_id=normalized.business_connection_id,
        title=None,
        happened_at=now,
    )
    stored_message = Message(
        bot_id=bot.id,
        account_id=account_id,
        project_id=DEFAULT_PROJECT_ID,
        gate_event_id=gate_event.id,
        direction=message_direction,
        source="telegram",
        event_type=normalized.event_type,
        chat_id=normalized.chat_id,
        sender_id=normalized.sender_id,
        recipient_id=normalized.chat_id if is_outbound else None,
        text=normalized.text,
        telegram_update_id=normalized.update_id,
        telegram_message_id=normalized.message_id,
        business_connection_id=normalized.business_connection_id,
        payload={"raw_update_ref": "telegram_updates"},
        received_at=None if is_outbound else now,
        sent_at=now if is_outbound else None,
    )
    session.add(stored_message)
    await session.flush()

    admin_command = legacy._parse_admin_console_command(normalized.text)
    if is_outbound:
        conversation.status = ConversationStatus.REPLIED.value
        conversation.requires_reply = False
        conversation.last_outbound_at = now
        conversation.last_message_at = now
        await legacy._satisfy_reply_tasks(
            session=session,
            conversation_id=conversation.id,
        )
    elif legacy._is_human_text_message(normalized) and admin_command is None:
        conversation.status = ConversationStatus.OPEN.value
        conversation.requires_reply = True
        conversation.last_inbound_at = now
        conversation.last_message_at = now
        session.add(
            ReplyTask(
                account_id=account_id,
                project_id=DEFAULT_PROJECT_ID,
                conversation_id=conversation.id,
                trigger_message_id=stored_message.id,
                status=ReplyTaskStatus.NEEDS_REPLY.value,
                reason="inbound_human_text",
            )
        )
        await session.flush()

    session.add(
        AuditLog(
            actor="telegram",
            action="telegram_business_update_received",
            bot_id=bot.id,
            account_id=account_id,
            project_id=DEFAULT_PROJECT_ID,
            gate_event_id=gate_event.id,
            metadata_={
                "event_type": normalized.event_type,
                "telegram_update_id": normalized.update_id,
                "telegram_message_id": normalized.message_id,
                "business_connection_id": normalized.business_connection_id,
                "direction": message_direction,
            },
        )
    )
    await session.commit()

    if is_outbound:
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": None,
            "duplicate": False,
            "send_status": "observed_outbound",
        }

    return await _maybe_send_business_reply(
        normalized=normalized,
        account_id=account_id,
        bot_id=bot.id,
        gate_event_id=gate_event.id,
        conversation=conversation,
        admin_command=admin_command,
        session=session,
        settings=settings,
        telegram=telegram,
    )


async def _maybe_send_business_reply(
    *,
    normalized: legacy.TelegramUpdateEvent,
    account_id: Any,
    bot_id: Any,
    gate_event_id: Any,
    conversation: Any,
    admin_command: legacy.TelegramAdminCommand | None,
    session: AsyncSession,
    settings: Settings,
    telegram: TelegramClient,
) -> JsonObject:
    auto_reply: str | None = None
    send_status = "not_applicable"

    if admin_command is not None:
        if legacy._is_telegram_admin_sender(normalized.sender_id):
            auto_reply = await legacy._build_telegram_admin_reply(
                command=admin_command,
                session=session,
                settings=settings,
            )
            audit_action = f"telegram_admin_console_{admin_command.name}_sent"
        else:
            auto_reply = "Gate online"
            audit_action = "telegram_admin_console_rejected"
        return await _send_business_text_reply(
            normalized=normalized,
            account_id=account_id,
            bot_id=bot_id,
            gate_event_id=gate_event_id,
            conversation=conversation,
            text=auto_reply,
            audit_action=audit_action,
            session=session,
            settings=settings,
            telegram=telegram,
        )

    if legacy._is_ping(normalized.text):
        auto_reply = "Gate online"
        return await _send_business_text_reply(
            normalized=normalized,
            account_id=account_id,
            bot_id=bot_id,
            gate_event_id=gate_event_id,
            conversation=conversation,
            text=auto_reply,
            audit_action="auto_safe_reply_sent",
            session=session,
            settings=settings,
            telegram=telegram,
        )

    return {
        "ok": True,
        "event_type": normalized.event_type,
        "auto_reply": auto_reply,
        "duplicate": False,
        "send_status": send_status,
    }


async def _send_business_text_reply(
    *,
    normalized: legacy.TelegramUpdateEvent,
    account_id: Any,
    bot_id: Any,
    gate_event_id: Any,
    conversation: Any,
    text: str,
    audit_action: str,
    session: AsyncSession,
    settings: Settings,
    telegram: TelegramClient,
) -> JsonObject:
    if not _is_business_send_enabled(account_id=account_id, settings=settings):
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_business_reply_blocked",
                bot_id=bot_id,
                account_id=account_id,
                project_id=DEFAULT_PROJECT_ID,
                gate_event_id=gate_event_id,
                metadata_={
                    "reason": "send_disabled",
                    "business_connection_id": normalized.business_connection_id,
                },
            )
        )
        await session.commit()
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": text,
            "duplicate": False,
            "send_status": "blocked",
        }

    try:
        send_result = await telegram.send_message(
            chat_id=normalized.chat_id,
            text=text,
            business_connection_id=normalized.business_connection_id,
        )
    except Exception as exc:
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_business_reply_failed",
                bot_id=bot_id,
                account_id=account_id,
                project_id=DEFAULT_PROJECT_ID,
                gate_event_id=gate_event_id,
                metadata_={
                    "error_type": exc.__class__.__name__,
                    "business_connection_id": normalized.business_connection_id,
                },
            )
        )
        await session.commit()
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": text,
            "duplicate": False,
            "send_status": "failed",
        }

    outbound_at = datetime.now(UTC)
    session.add(
        Message(
            bot_id=bot_id,
            account_id=account_id,
            project_id=DEFAULT_PROJECT_ID,
            gate_event_id=gate_event_id,
            direction=MessageDirection.OUTBOUND.value,
            source="gate",
            event_type="telegram_business_reply",
            chat_id=normalized.chat_id,
            sender_id=settings.telegram_bot_username,
            recipient_id=normalized.chat_id,
            text=text,
            business_connection_id=normalized.business_connection_id,
            payload=send_result,
            sent_at=outbound_at,
        )
    )
    conversation.status = ConversationStatus.REPLIED.value
    conversation.requires_reply = False
    conversation.last_outbound_at = outbound_at
    conversation.last_message_at = outbound_at
    await legacy._satisfy_reply_tasks(session=session, conversation_id=conversation.id)
    session.add(
        AuditLog(
            actor="gate",
            action=audit_action,
            bot_id=bot_id,
            account_id=account_id,
            project_id=DEFAULT_PROJECT_ID,
            gate_event_id=gate_event_id,
            metadata_={"business_connection_id": normalized.business_connection_id},
        )
    )
    await session.commit()
    return {
        "ok": True,
        "event_type": normalized.event_type,
        "auto_reply": text,
        "duplicate": False,
        "send_status": "sent",
    }


async def _find_owner_identity_ids(
    *,
    session: AsyncSession,
    account_id: Any,
) -> frozenset[str]:
    owner_result = await session.execute(select(Account.owner).where(Account.id == account_id))
    owner = owner_result.scalar_one_or_none()
    if owner is None:
        return frozenset()

    identity_result = await session.execute(
        select(
            Account.telegram_user_id,
            TelegramBusinessConnection.telegram_user_id,
            TelegramBusinessConnection.user_chat_id,
        )
        .outerjoin(
            TelegramBusinessConnection,
            TelegramBusinessConnection.account_id == Account.id,
        )
        .where(
            Account.owner == owner,
            Account.is_active.is_(True),
        )
    )
    return frozenset(
        str(value) for row in identity_result.all() for value in row if value is not None
    )


def _is_outbound_business_message(
    *,
    sender_id: str,
    owner_identity_ids: frozenset[str],
) -> bool:
    return str(sender_id) in owner_identity_ids


async def _resolve_account_id(
    *,
    session: AsyncSession,
    business_connection_id: str | None,
    settings: Settings,
) -> Any:
    configured = _account_id_from_configured_connection(
        business_connection_id,
        settings=settings,
    )
    if configured is not None:
        return configured
    if business_connection_id:
        result = await session.execute(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.business_connection_id
                == business_connection_id,
                TelegramBusinessConnection.is_enabled.is_(True),
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing.account_id
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Unknown Telegram Business connection. Configure its Gate account mapping "
            "before processing messages."
        ),
    )


async def _resolve_business_connection_account_id(
    *,
    session: AsyncSession,
    update: JsonObject,
    business_connection_id: str | None,
    settings: Settings,
) -> Any:
    configured = _account_id_from_configured_connection(
        business_connection_id,
        settings=settings,
    )
    if configured is not None:
        return configured

    if business_connection_id:
        existing_result = await session.execute(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.business_connection_id
                == business_connection_id
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing.account_id

    connection = update.get("business_connection")
    connection_data = cast(JsonObject, connection) if isinstance(connection, dict) else {}
    user = connection_data.get("user")
    user_data = cast(JsonObject, user) if isinstance(user, dict) else {}
    telegram_user_id = legacy._optional_int(user_data.get("id"))
    if telegram_user_id is not None:
        account_result = await session.execute(
            select(Account).where(Account.telegram_user_id == telegram_user_id)
        )
        account = account_result.scalar_one_or_none()
        if account is not None:
            return account.id

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Unknown Telegram Business owner. Link the Telegram user to a Gate "
            "account before accepting this connection."
        ),
    )
def _account_id_from_configured_connection(
    business_connection_id: str | None,
    *,
    settings: Settings,
) -> Any | None:
    if not business_connection_id:
        return None
    if business_connection_id == settings.gate_account_ru_business_connection_id:
        return TELEGRAM_MARK_RU_ACCOUNT_ID
    if business_connection_id == settings.gate_account_by_business_connection_id:
        return TELEGRAM_MARK_BY_ACCOUNT_ID
    return None


async def _upsert_business_connection(
    *,
    session: AsyncSession,
    account_id: Any,
    update: JsonObject,
    received_at: datetime,
) -> None:
    business_connection = cast(JsonObject, update.get("business_connection"))
    business_connection_id = legacy._optional_string(business_connection.get("id"))
    if business_connection_id is None:
        return

    result = await session.execute(
        select(TelegramBusinessConnection).where(
            TelegramBusinessConnection.business_connection_id == business_connection_id
        )
    )
    row = result.scalar_one_or_none()
    user = business_connection.get("user")
    user_data = cast(JsonObject, user) if isinstance(user, dict) else {}
    raw_rights = business_connection.get("rights")
    rights = cast(JsonObject, raw_rights) if isinstance(raw_rights, dict) else {}
    is_enabled = bool(business_connection.get("is_enabled", True))
    if is_enabled:
        await session.execute(
            sql_update(TelegramBusinessConnection)
            .where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.business_connection_id
                != business_connection_id,
                TelegramBusinessConnection.is_enabled.is_(True),
            )
            .values(
                is_enabled=False,
                last_error="replaced_by_new_business_connection",
                last_update_at=received_at,
            )
        )
    if row is None:
        row = TelegramBusinessConnection(
            account_id=account_id,
            business_connection_id=business_connection_id,
            telegram_user_id=legacy._optional_int(user_data.get("id")),
            user_chat_id=legacy._optional_int(business_connection.get("user_chat_id")),
            rights=rights,
            is_enabled=is_enabled,
            received_at=received_at,
            last_update_at=received_at,
        )
        session.add(row)
        return

    row.account_id = account_id
    row.telegram_user_id = legacy._optional_int(user_data.get("id"))
    row.user_chat_id = legacy._optional_int(business_connection.get("user_chat_id"))
    row.rights = rights
    row.is_enabled = is_enabled
    row.last_update_at = received_at
    if is_enabled:
        row.last_error = None


def _is_business_send_enabled(*, account_id: Any, settings: Settings) -> bool:
    if not settings.gate_send_enabled:
        return False
    if account_id == TELEGRAM_MARK_RU_ACCOUNT_ID:
        return settings.gate_account_ru_send_enabled
    if account_id == TELEGRAM_MARK_BY_ACCOUNT_ID:
        return settings.gate_account_by_send_enabled
    return False


def _is_business_update(update: JsonObject) -> bool:
    if isinstance(update.get("business_connection"), dict):
        return True
    if isinstance(update.get("business_message"), dict):
        return True
    message = update.get("message")
    return isinstance(message, dict) and message.get("business_connection_id") is not None


def _require_telegram_secret(*, x_telegram_secret: str | None) -> None:
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


async def _read_update(request: Request) -> JsonObject:
    raw_update = await request.json()
    if not isinstance(raw_update, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram update",
        )
    return cast(JsonObject, raw_update)
