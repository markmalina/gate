"""Separate authenticated durable ingress for the Beta group bot."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.beta_space_intake import (
    DeliverBetaSpaceConsent,
    ProcessBetaSpaceUpdate,
)
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    TelegramReceiptAcceptance,
    TelegramReceiptError,
    TelegramReceiptStore,
)
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.beta_space import (
    SqlBetaSpaceStore,
    beta_space_production_ready,
)
from app.services.telegram import TelegramClient

BETA_TELEGRAM_WEBHOOK_PATH = "/telegram/beta/webhook"
TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807

router = APIRouter()
settings_dependency = Depends(get_settings)
receipt_store_dependency = Depends(get_telegram_receipt_store)
db_session_dependency = Depends(get_db_session)
JsonObject = dict[str, Any]


def get_beta_telegram_client(request: Request) -> TelegramClient:
    telegram = getattr(request.app.state, "beta_telegram_client", None)
    if not isinstance(telegram, TelegramClient):
        raise RuntimeError("Beta Telegram client is not initialized")
    return telegram


def get_beta_space_store(
    session: AsyncSession = db_session_dependency,
) -> SqlBetaSpaceStore:
    return SqlBetaSpaceStore(session)


beta_space_store_dependency = Depends(get_beta_space_store)


@router.post(BETA_TELEGRAM_WEBHOOK_PATH)
async def beta_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    receipt_store: TelegramReceiptStore = receipt_store_dependency,
    beta_space_store: SqlBetaSpaceStore = beta_space_store_dependency,
) -> JsonObject:
    """Authenticate and durably accept one group-only update without legacy dispatch."""

    try:
        raw_update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from None
    if not isinstance(raw_update, dict):
        raise HTTPException(status_code=400, detail="Invalid Telegram update")
    received_at = datetime.now(UTC)
    update = cast(JsonObject, raw_update)
    acceptance = await accept_beta_telegram_receipt(
        update=update,
        provided_secret=x_telegram_secret,
        settings=settings,
        receipt_store=receipt_store,
        received_at=received_at,
    )
    if acceptance is None:
        return {
            "ok": True,
            "accepted": False,
            "ignored": True,
            "reason": "unsupported_update",
        }
    intake = await ProcessBetaSpaceUpdate(
        store=beta_space_store,
        production_ready=beta_space_production_ready(settings),
    ).execute(
        update=update,
        receipt_id=acceptance.receipt_id,
        received_at=received_at,
    )
    delivery = None
    if intake.delivery_id is not None:
        delivery = await DeliverBetaSpaceConsent(
            store=beta_space_store,
            telegram=get_beta_telegram_client(request),
            enabled=beta_space_production_ready(settings),
        ).execute(delivery_id=intake.delivery_id, now=received_at)
    return {
        "ok": True,
        "accepted": True,
        "duplicate": acceptance.duplicate,
        "space_processing": intake.disposition.value,
        "delivery": delivery,
    }


async def accept_beta_telegram_receipt(
    *,
    update: JsonObject,
    provided_secret: str | None,
    settings: Settings,
    receipt_store: TelegramReceiptStore,
    received_at: datetime,
) -> TelegramReceiptAcceptance | None:
    """Authenticate every update and persist only approved group shapes."""

    if not settings.gate_beta_connector_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Beta connector is disabled",
        )
    configured_secret = settings.telegram_beta_webhook_secret.get_secret_value()
    if (
        not configured_secret
        or provided_secret is None
        or not _secrets_match(provided=provided_secret, configured=configured_secret)
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update_id = update.get("update_id")
    if type(update_id) is not int or update_id < 0 or update_id > TELEGRAM_UPDATE_ID_MAX:
        raise HTTPException(status_code=400, detail="Invalid Telegram update id")
    if not _is_group_update(update):
        # Telegram retries every non-2xx webhook response. Authenticated updates
        # outside this group-only connector are terminally acknowledged by the
        # route without persistence so one poison item cannot block the queue.
        return None
    try:
        return await AcceptTelegramReceiptService(receipt_store).execute(
            AcceptTelegramReceiptCommand(
                bot_key=TelegramConnectorKey.BETA_GROUP.value,
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
