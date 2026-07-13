# mypy: ignore-errors
# ruff: noqa
"""Sprint 18b Telegram draft UX copy."""

from datetime import UTC, datetime
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes as legacy
from app.api import sprint14_routes as sprint14
from app.api import sprint17_send_draft_routes as sprint17
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.scoped_admin import build_scoped_telegram_admin_reply, resolve_telegram_admin_context
from app.application.telegram_ingress_shadow import observe_telegram_ingress_shadow
from app.core.config import Settings, get_settings
from app.models.domain import AuditLog, TelegramUpdate
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]


@router.post("/telegram/webhook")
async def sprint18b_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Handle /draft with clearer tap-first action copy."""

    update = await sprint14._read_update(request)
    observe_telegram_ingress_shadow(update, logger=logger)
    normalized = legacy._extract_update_event(update)
    command = legacy._parse_admin_console_command(normalized.text if normalized else None)
    if normalized is None or command is None or command.name != "draft":
        return await sprint17.sprint17_telegram_webhook(
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
        base_reply = await build_scoped_telegram_admin_reply(
            command=command,
            admin_context=admin_context,
            session=session,
            settings=settings,
        )
        reply_text = _enhance_draft_reply(base_reply=base_reply, task_ref=command.task_ref)
        audit_action = "telegram_admin_console_draft_ux_sent"
    else:
        reply_text = "Gate online"
        audit_action = "telegram_admin_console_rejected"

    try:
        send_result = await telegram.send_message(
            chat_id=normalized.chat_id,
            text=reply_text,
            business_connection_id=normalized.business_connection_id,
        )
        send_status = "sent"
    except Exception as exc:
        send_result = None
        send_status = "failed"
        session.add(
            AuditLog(
                actor="gate",
                action="telegram_admin_console_reply_failed",
                bot_id=bot.id,
                gate_event_id=None,
                metadata_={"command": normalized.text, "error_type": exc.__class__.__name__},
            )
        )

    session.add(
        AuditLog(
            actor="gate",
            action=audit_action,
            bot_id=bot.id,
            gate_event_id=None,
            metadata_={
                "command": normalized.text,
                "task_ref": command.task_ref,
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
        "send_status": send_status,
    }


def _enhance_draft_reply(*, base_reply: str, task_ref: str | None) -> str:
    draft_ref = _extract_draft_ref(base_reply)
    if not draft_ref:
        return base_reply
    task = task_ref or "<task>"
    cleaned_reply = _strip_existing_actions(base_reply)
    return "\n".join(
        [
            cleaned_reply,
            "",
            "Действия:",
            f"✅ Отправить: /send_{draft_ref}",
            f"📄 Все драфты: /drafts_{task}",
            f"✏️ Новый драфт: /draft_{task}",
            f"✅ Закрыть как отвеченное: /done_{task}",
            f"❌ Закрыть без ответа: /dismiss_{task}",
            f"⏸ Отложить: /snooze_{task}",
            "",
            "Нажимай синюю команду. ID руками вводить не нужно.",
        ]
    )


def _strip_existing_actions(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []
    skip = False
    for line in lines:
        normalized = line.strip().casefold()
        if normalized in {"actions:", "действия:"}:
            skip = True
            continue
        if skip:
            if normalized.startswith("наж") or normalized.startswith("press "):
                skip = False
                continue
            if normalized.startswith(("/", "✅", "👀", "📄", "✏️", "❌", "⏸")):
                continue
            if any(token in normalized for token in ("/send_", "/draft", "/done_", "/dismiss_", "/snooze_")):
                continue
            skip = False
        result.append(line)
    return "\n".join(result).rstrip()


def _extract_draft_ref(text: str) -> str | None:
    for line in text.splitlines():
        normalized = line.strip()
        prefix = "Draft created:"
        if normalized.startswith(prefix):
            value = normalized.removeprefix(prefix).strip()
            return value or None
    return None
