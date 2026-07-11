# mypy: ignore-errors
"""Sprint 14b Telegram status with Business connection visibility."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import Settings, get_settings
from app.models.domain import (
    TELEGRAM_MARK_BY_ACCOUNT_ID,
    TELEGRAM_MARK_RU_ACCOUNT_ID,
    Account,
    AuditLog,
    GateEvent,
    Message,
    MessageDirection,
    TelegramBusinessConnection,
    TelegramUpdate,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class BusinessAssignmentCommand:
    """Admin command assigning a stored Business connection to a Gate account."""

    account_alias: str
    connection_ref: str


@router.post("/telegram/webhook")
async def sprint14b_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle Telegram admin commands with Business connection visibility."""

    update = await sprint14._read_update(request)
    normalized = legacy._extract_update_event(update)
    admin_command = legacy._parse_admin_console_command(normalized.text if normalized else None)
    assignment_command = _parse_business_assignment_command(
        normalized.text if normalized else None
    )
    if admin_command is None and assignment_command is None:
        return await sprint14.sprint14_telegram_webhook(
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

    business_connections: list[JsonObject] = []
    include_empty_business_connections = settings.gate_env != "development"
    is_admin_context = await _is_telegram_admin_context(
        normalized=normalized,
        session=session,
    )
    if is_admin_context:
        if assignment_command is not None:
            reply_text = await _assign_business_connection(
                command=assignment_command,
                session=session,
            )
            audit_action = "telegram_business_connection_assigned"
        elif admin_command is not None and admin_command.name == "status":
            status_payload = await legacy._build_admin_status_payload(
                session=session,
                settings=settings,
            )
            business_connections = await _load_business_connection_statuses(
                session=session,
                settings=settings,
            )
            reply_text = _format_status_with_business_connections(
                status_payload=status_payload,
                business_connections=business_connections,
                include_empty=include_empty_business_connections,
            )
            audit_action = "telegram_admin_console_status_sent"
        elif admin_command is not None:
            reply_text = await legacy._build_telegram_admin_reply(
                command=admin_command,
                session=session,
                settings=settings,
            )
            audit_action = f"telegram_admin_console_{admin_command.name}_sent"
        else:
            reply_text = "Gate online"
            audit_action = "telegram_admin_console_rejected"
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
                gate_event_id=gate_event.id,
                metadata_={
                    "command": normalized.text,
                    "error_type": exc.__class__.__name__,
                },
            )
        )
        await session.commit()
        return {
            "ok": True,
            "event_type": normalized.event_type,
            "auto_reply": reply_text,
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
            text=reply_text,
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
            metadata_={
                "command": normalized.text,
                "business_connections_included": bool(business_connections)
                or include_empty_business_connections,
                "business_connections_count": len(business_connections),
                "admin_context": is_admin_context,
            },
        )
    )
    await session.commit()

    return {
        "ok": True,
        "event_type": normalized.event_type,
        "auto_reply": reply_text,
        "duplicate": False,
        "send_status": "sent",
    }


async def _load_business_connection_statuses(
    *,
    session: AsyncSession,
    settings: Settings,
) -> list[JsonObject]:
    result = await session.execute(
        select(TelegramBusinessConnection, Account)
        .join(Account, TelegramBusinessConnection.account_id == Account.id)
        .order_by(Account.alias.asc(), TelegramBusinessConnection.last_update_at.desc())
    )
    rows = []
    for connection, account in result.all():
        rows.append(
            {
                "account_alias": account.alias,
                "business_connection_id": connection.business_connection_id,
                "configured": _is_configured_connection(
                    account_alias=account.alias,
                    business_connection_id=connection.business_connection_id,
                    settings=settings,
                ),
                "is_enabled": connection.is_enabled,
                "send_enabled": _send_enabled_for_account_alias(
                    account_alias=account.alias,
                    settings=settings,
                ),
                "telegram_user_id": connection.telegram_user_id,
                "user_chat_id": connection.user_chat_id,
                "can_reply": connection.rights.get("can_reply"),
                "last_update_at": legacy._serialize_datetime(connection.last_update_at),
            }
        )
    return rows


async def _is_telegram_admin_context(
    *,
    normalized: legacy.TelegramUpdateEvent,
    session: AsyncSession,
) -> bool:
    if legacy._is_telegram_admin_sender(normalized.sender_id):
        return True

    sender_id = legacy._optional_int(normalized.sender_id)
    if sender_id is None:
        return False

    result = await session.execute(
        select(TelegramBusinessConnection).where(
            TelegramBusinessConnection.user_chat_id == sender_id,
            TelegramBusinessConnection.is_enabled.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def _assign_business_connection(
    *,
    command: BusinessAssignmentCommand,
    session: AsyncSession,
) -> str:
    account_id = _account_id_from_alias(command.account_alias)
    if account_id is None:
        return f"Unknown Business account alias: {command.account_alias}"

    result = await session.execute(
        select(TelegramBusinessConnection).where(
            TelegramBusinessConnection.business_connection_id == command.connection_ref
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        return "\n".join(
            [
                "Business connection not found.",
                f"id: {command.connection_ref}",
                "Use /status and copy the full id exactly.",
            ]
        )

    if connection.is_enabled:
        enabled_result = await session.execute(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.is_enabled.is_(True),
            )
        )
        for enabled_connection in enabled_result.scalars().all():
            if enabled_connection.business_connection_id != connection.business_connection_id:
                enabled_connection.is_enabled = False
                enabled_connection.last_update_at = datetime.now(UTC)

    connection.account_id = account_id
    connection.last_update_at = datetime.now(UTC)
    return "\n".join(
        [
            "Business connection assigned",
            f"account: {command.account_alias}",
            f"id: {connection.business_connection_id}",
            f"enabled: {_yes_no(connection.is_enabled)}",
            f"can_reply: {_optional_bool(connection.rights.get('can_reply'))}",
            f"user_chat_id: {connection.user_chat_id or 'unknown'}",
        ]
    )


def _format_status_with_business_connections(
    *,
    status_payload: JsonObject,
    business_connections: list[JsonObject],
    include_empty: bool = True,
) -> str:
    lines = [legacy._format_telegram_admin_status(status_payload)]
    if not business_connections and not include_empty:
        return "\n".join(lines)

    lines.extend(["", "Business connections:"])
    if not business_connections:
        lines.append("- none stored yet")
        return "\n".join(lines)

    for item in business_connections:
        connection_id = str(item["business_connection_id"])
        lines.extend(
            [
                f"- {item['account_alias']}",
                f"  id: {connection_id}",
                f"  enabled: {_yes_no(item['is_enabled'])}",
                f"  configured: {_yes_no(item['configured'])}",
                f"  send: {_yes_no(item['send_enabled'])}",
                f"  can_reply: {_optional_bool(item['can_reply'])}",
                f"  user_chat_id: {item['user_chat_id'] or 'unknown'}",
                f"  last_update: {item['last_update_at'] or 'unknown'}",
            ]
        )
    return "\n".join(lines)


def _parse_business_assignment_command(text: str | None) -> BusinessAssignmentCommand | None:
    normalized = (text or "").strip()
    if not normalized:
        return None

    parts = normalized.split(maxsplit=2)
    if len(parts) == 3 and parts[0].casefold() == "/business":
        account_alias = _normalize_business_account_alias(parts[1])
        if account_alias is not None:
            return BusinessAssignmentCommand(
                account_alias=account_alias,
                connection_ref=parts[2].strip(),
            )

    parts = normalized.split(maxsplit=1)
    if len(parts) == 2:
        prefix_aliases = {
            "/business_ru": "telegram_mark_ru",
            "/business_by": "telegram_mark_by",
        }
        account_alias = prefix_aliases.get(parts[0].casefold())
        if account_alias is not None:
            return BusinessAssignmentCommand(
                account_alias=account_alias,
                connection_ref=parts[1].strip(),
            )

    compact_prefixes = {
        "/business_ru_": "telegram_mark_ru",
        "/business_by_": "telegram_mark_by",
    }
    normalized_casefold = normalized.casefold()
    for prefix, account_alias in compact_prefixes.items():
        if normalized_casefold.startswith(prefix):
            return BusinessAssignmentCommand(
                account_alias=account_alias,
                connection_ref=normalized[len(prefix) :].strip(),
            )
    return None


def _normalize_business_account_alias(value: str) -> str | None:
    normalized = value.strip().casefold()
    aliases = {
        "ru": "telegram_mark_ru",
        "ру": "telegram_mark_ru",
        "telegram_mark_ru": "telegram_mark_ru",
        "by": "telegram_mark_by",
        "бу": "telegram_mark_by",
        "бел": "telegram_mark_by",
        "telegram_mark_by": "telegram_mark_by",
    }
    return aliases.get(normalized)


def _account_id_from_alias(account_alias: str) -> Any | None:
    if account_alias == "telegram_mark_ru":
        return TELEGRAM_MARK_RU_ACCOUNT_ID
    if account_alias == "telegram_mark_by":
        return TELEGRAM_MARK_BY_ACCOUNT_ID
    return None


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _optional_bool(value: Any) -> str:
    if value is None:
        return "unknown"
    return _yes_no(value)


def _is_configured_connection(
    *,
    account_alias: str,
    business_connection_id: str,
    settings: Settings,
) -> bool:
    if account_alias == "telegram_mark_ru":
        return business_connection_id == settings.gate_account_ru_business_connection_id
    if account_alias == "telegram_mark_by":
        return business_connection_id == settings.gate_account_by_business_connection_id
    return False


def _send_enabled_for_account_alias(*, account_alias: str, settings: Settings) -> bool:
    if not settings.gate_send_enabled:
        return False
    if account_alias == "telegram_mark_ru":
        return settings.gate_account_ru_send_enabled
    if account_alias == "telegram_mark_by":
        return settings.gate_account_by_send_enabled
    return False
