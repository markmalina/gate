"""Durable Telegram ingress with staged canonical update-class ownership.

The frozen sprint routers still own update classes that have not completed canonical cutover.
"""

import hmac
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import sprint18b_draft_ux_routes as sprint18b
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.api.sprint9_routes import router as sprint9_router
from app.api.sprint10_routes import router as sprint10_router
from app.api.sprint11b_routes import router as sprint11b_router
from app.api.sprint14_routes import router as sprint14_router
from app.api.sprint14b_routes import router as sprint14b_router
from app.api.sprint15b_routes import router as sprint15b_router
from app.api.sprint16_raw_routes import router as sprint16_raw_router
from app.api.sprint16d_clean_inbox_routes import router as sprint16d_clean_inbox_router
from app.api.sprint17_send_draft_routes import router as sprint17_send_draft_router
from app.application.telegram_business_text_inbox import (
    is_telegram_business_text_cutover_update,
)
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    TelegramReceiptError,
    TelegramReceiptStore,
)
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.services.telegram import TelegramClient

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807
logger = logging.getLogger(__name__)

durable_ingress_router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]
LegacyTelegramWebhook = Callable[..., Awaitable[JsonObject]]
legacy_telegram_webhook: LegacyTelegramWebhook = sprint18b.sprint18b_telegram_webhook


def get_telegram_receipt_store(
    session: AsyncSession = db_session_dependency,
) -> TelegramReceiptStore:
    """Compose the existing atomic PostgreSQL receipt adapter for one request."""

    return SqlTelegramReceiptStore(session)


receipt_store_dependency = Depends(get_telegram_receipt_store)


@durable_ingress_router.post(TELEGRAM_WEBHOOK_PATH)
async def durable_telegram_webhook(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
    receipt_store: TelegramReceiptStore = receipt_store_dependency,
) -> JsonObject:
    """Durably accept every authenticated update before the frozen legacy chain."""

    try:
        accepted_update = await _accept_durable_receipt(
            request=request,
            x_telegram_secret=x_telegram_secret,
            settings=settings,
            receipt_store=receipt_store,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log_acceptance_failure(exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Durable Telegram receipt is unavailable",
        ) from None

    if _is_business_voice_update(accepted_update) or is_telegram_business_text_cutover_update(
        accepted_update
    ):
        return {
            "ok": True,
            "accepted": True,
            "processing": "durable_worker",
        }

    return await legacy_telegram_webhook(
        request=request,
        x_telegram_secret=x_telegram_secret,
        settings=settings,
        session=session,
        telegram=telegram,
    )


async def _accept_durable_receipt(
    *,
    request: Request,
    x_telegram_secret: str | None,
    settings: Settings,
    receipt_store: TelegramReceiptStore,
) -> JsonObject:
    configured_secret = settings.telegram_webhook_secret.get_secret_value()
    if not configured_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook secret is not configured",
        )
    if x_telegram_secret is None or not _secrets_match(
        provided=x_telegram_secret,
        configured=configured_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret",
        )

    try:
        raw_update = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram update",
        ) from None
    if not isinstance(raw_update, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram update",
        )

    update = cast(JsonObject, raw_update)
    update_id = update.get("update_id")
    if type(update_id) is not int or update_id < 0 or update_id > TELEGRAM_UPDATE_ID_MAX:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram update id",
        )

    try:
        await AcceptTelegramReceiptService(receipt_store).execute(
            AcceptTelegramReceiptCommand(
                bot_key=TelegramConnectorKey.BUSINESS.value,
                update_id=update_id,
                payload=update,
                received_at=datetime.now(UTC),
            )
        )
    except TelegramReceiptError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram receipt envelope",
        ) from None
    except Exception as exc:
        _log_acceptance_failure(exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Durable Telegram receipt is unavailable",
        ) from None
    return update


def _is_business_voice_update(update: JsonObject) -> bool:
    """Route the canonical voice class exclusively through the durable worker."""

    message = update.get("business_message")
    return isinstance(message, dict) and "voice" in message


def _secrets_match(*, provided: str, configured: str) -> bool:
    """Compare arbitrary header text without allowing a TypeError to escape."""

    try:
        provided_bytes = provided.encode("utf-8")
        configured_bytes = configured.encode("utf-8")
    except UnicodeError:
        return False
    return hmac.compare_digest(provided_bytes, configured_bytes)


def _log_acceptance_failure(exc: Exception) -> None:
    logger.error(
        "Durable Telegram receipt acceptance failed: error_type=%s",
        exc.__class__.__name__,
    )


# FastAPI dispatches duplicate path/method registrations in registration order. The first
# matching sprint layer handles its command or delegates directly to the next layer.
LEGACY_TELEGRAM_INGRESS_ROUTERS: tuple[APIRouter, ...] = (
    sprint18b.router,
    sprint17_send_draft_router,
    sprint16d_clean_inbox_router,
    sprint16_raw_router,
    sprint15b_router,
    sprint14b_router,
    sprint14_router,
    sprint11b_router,
    sprint10_router,
    sprint9_router,
)

# Characterization fingerprint. The base webhook in app.api.routes is registered after the
# transitional sprint layers and terminates the delegation chain.
LEGACY_TELEGRAM_WEBHOOK_CHAIN: tuple[str, ...] = (
    "app.api.sprint18b_draft_ux_routes:sprint18b_telegram_webhook",
    "app.api.sprint17_send_draft_routes:sprint17_telegram_webhook",
    "app.api.sprint16d_clean_inbox_routes:sprint16d_telegram_webhook",
    "app.api.sprint16_raw_routes:sprint16_telegram_webhook",
    "app.api.sprint15b_routes:sprint15b_telegram_webhook",
    "app.api.sprint14b_routes:sprint14b_telegram_webhook",
    "app.api.sprint14_routes:sprint14_telegram_webhook",
    "app.api.sprint11b_routes:sprint11b_telegram_webhook",
    "app.api.sprint10_routes:sprint10_telegram_webhook",
    "app.api.sprint9_routes:sprint9_telegram_webhook",
    "app.api.routes:telegram_webhook",
)

TELEGRAM_WEBHOOK_CHAIN: tuple[str, ...] = (
    "app.api.telegram_ingress:durable_telegram_webhook",
    *LEGACY_TELEGRAM_WEBHOOK_CHAIN,
)


def include_telegram_ingress(app: FastAPI) -> None:
    """Register the public durable seam before the frozen legacy stack."""

    from app.api.alpha_telegram_ingress import router as alpha_router
    from app.api.aqub_telegram_preflight import router as aqub_router
    from app.api.become_telegram_ingress import router as become_router
    from app.api.beta_telegram_ingress import router as beta_router

    app.include_router(aqub_router)
    app.include_router(alpha_router)
    app.include_router(beta_router)
    app.include_router(become_router)
    app.include_router(durable_ingress_router)
    include_legacy_telegram_ingress(app)


def include_legacy_telegram_ingress(app: FastAPI) -> None:
    """Register the frozen webhook delegation stack in production order."""

    for router in LEGACY_TELEGRAM_INGRESS_ROUTERS:
        app.include_router(router)
