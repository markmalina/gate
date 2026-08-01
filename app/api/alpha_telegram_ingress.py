"""Separate authenticated durable ingress for the Alpha bot."""

from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    TelegramReceiptAcceptance,
    TelegramReceiptError,
    TelegramReceiptStore,
)
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.alpha_delivery import TelegramClientAlphaTextAdapter
from app.infrastructure.alpha_owner_private_recovery import (
    SqlAlphaOwnerPrivateRecovery,
    authorize_owner_private_update,
)
from app.services.telegram import TelegramClient, build_telegram_http_client

ALPHA_TELEGRAM_WEBHOOK_PATH = "/telegram/alpha/webhook"
TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
receipt_store_dependency = Depends(get_telegram_receipt_store)
JsonObject = dict[str, Any]


@router.post(ALPHA_TELEGRAM_WEBHOOK_PATH)
async def alpha_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    receipt_store: TelegramReceiptStore = receipt_store_dependency,
) -> JsonObject:
    """Authenticate and durably accept group or exact owner-private updates."""

    try:
        raw_update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from None
    if not isinstance(raw_update, dict):
        raise HTTPException(status_code=400, detail="Invalid Telegram update")
    update = cast(JsonObject, raw_update)
    if not settings.gate_alpha_connector_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Alpha connector is disabled",
        )
    configured_secret = settings.telegram_alpha_webhook_secret.get_secret_value()
    if (
        not configured_secret
        or x_telegram_secret is None
        or not _secrets_match(provided=x_telegram_secret, configured=configured_secret)
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    owner_chat_id = None
    if not _is_group_update(update):
        owner_chat_id = await authorize_owner_private_update(session, update)
        if owner_chat_id is None:
            raise HTTPException(status_code=400, detail="Unsupported Alpha update")
    acceptance = await accept_alpha_telegram_receipt(
        update=update,
        provided_secret=x_telegram_secret,
        settings=settings,
        receipt_store=receipt_store,
        received_at=datetime.now(UTC),
        allow_owner_private=owner_chat_id is not None,
    )
    if owner_chat_id is None:
        return {"ok": True, "accepted": True, "duplicate": acceptance.duplicate}

    now = datetime.now(UTC)
    token = settings.telegram_alpha_bot_token.get_secret_value()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Alpha credentials are unavailable",
        )
    async with build_telegram_http_client() as http_client:
        telegram = TelegramClient(
            credential=token,
            api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
            http_client=http_client,
        )
        recovery = SqlAlphaOwnerPrivateRecovery(
            session,
            TelegramClientAlphaTextAdapter(telegram),
        )
        await recovery.complete_receipt(
            receipt_id=acceptance.receipt_id,
            outbox_event_id=acceptance.outbox_event_id,
            now=now,
        )
        result = await recovery.execute(target_chat_id=owner_chat_id, now=now)
    return {
        "ok": True,
        "accepted": True,
        "duplicate": acceptance.duplicate,
        "owner_private_recovery": {
            "sent": result.sent,
            "already_terminal": result.already_terminal,
            "failed": result.failed,
        },
    }


async def accept_alpha_telegram_receipt(
    *,
    update: JsonObject,
    provided_secret: str | None,
    settings: Settings,
    receipt_store: TelegramReceiptStore,
    received_at: datetime,
    allow_owner_private: bool = False,
) -> TelegramReceiptAcceptance:
    """Fail closed before persistence unless the separate connector is ready."""

    if not settings.gate_alpha_connector_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Alpha connector is disabled",
        )
    configured_secret = settings.telegram_alpha_webhook_secret.get_secret_value()
    if (
        not configured_secret
        or provided_secret is None
        or not _secrets_match(provided=provided_secret, configured=configured_secret)
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update_id = update.get("update_id")
    if type(update_id) is not int or update_id < 0 or update_id > TELEGRAM_UPDATE_ID_MAX:
        raise HTTPException(status_code=400, detail="Invalid Telegram update id")
    if not _is_group_update(update) and not allow_owner_private:
        raise HTTPException(status_code=400, detail="Alpha accepts group updates only")
    try:
        return await AcceptTelegramReceiptService(receipt_store).execute(
            AcceptTelegramReceiptCommand(
                bot_key=TelegramConnectorKey.ALPHA_GROUP.value,
                update_id=update_id,
                payload=update,
                received_at=received_at,
            )
        )
    except TelegramReceiptError:
        raise HTTPException(status_code=400, detail="Invalid Telegram receipt envelope") from None
    except Exception:
        raise HTTPException(
            status_code=503, detail="Durable Telegram receipt is unavailable"
        ) from None


def _is_group_update(update: JsonObject) -> bool:
    update_kinds = {key for key in update if key != "update_id"}
    if update_kinds not in ({"message"}, {"my_chat_member"}):
        return False
    message = update.get("message")
    if isinstance(message, dict):
        chat = message.get("chat")
        return isinstance(chat, dict) and chat.get("type") in {"group", "supergroup"}
    membership = update.get("my_chat_member")
    if isinstance(membership, dict):
        chat = membership.get("chat")
        return isinstance(chat, dict) and chat.get("type") in {"group", "supergroup"}
    return False


def _secrets_match(*, provided: str, configured: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode(), configured.encode())
    except UnicodeError:
        return False
