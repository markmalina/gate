"""Separate authenticated durable ingress for the Beta group bot."""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.beta_space_intake import (
    BetaSpaceDisposition,
    ProcessBetaSpaceUpdate,
)
from app.application.beta_wedding_reply import (
    BetaWeddingContextMessage,
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
from app.infrastructure.beta_candidate_binding import (
    extract_beta_callback_owner_candidate,
)
from app.infrastructure.beta_checkpoint_dispatch import (
    GitHubAppBetaCheckpointDispatcher,
)
from app.infrastructure.beta_checkpoint_dispatch_drain import (
    SessionFactoryBetaCheckpointDispatchDrain,
)
from app.infrastructure.beta_material_provider import (
    TelegramMaterialDownloadAdapter,
)
from app.infrastructure.beta_space import (
    SqlBetaSpaceStore,
    beta_space_production_ready,
)
from app.infrastructure.beta_wedding_reply import (
    BetaWeddingModelError,
)
from app.services.telegram import TelegramClient

BETA_TELEGRAM_WEBHOOK_PATH = "/telegram/beta/webhook"
TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807

logger = logging.getLogger(__name__)

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


def get_beta_model_http_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "beta_model_http_client", None)
    if not isinstance(client, httpx.AsyncClient):
        raise RuntimeError("Beta model HTTP client is not initialized")
    return client


class _UnavailableBetaWeddingModel:
    async def propose(
        self,
        *,
        current: BetaWeddingContextMessage,
        context: tuple[BetaWeddingContextMessage, ...],
    ) -> str:
        del current, context
        raise BetaWeddingModelError("beta_wedding_model_unavailable")


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
    session: AsyncSession = db_session_dependency,
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
    candidate_queued = False
    if intake.disposition is BetaSpaceDisposition.UNBOUND_CHAT:
        callback_owner = extract_beta_callback_owner_candidate(update)
        if callback_owner is not None:
            candidate_queued = await beta_space_store.enqueue_candidate_consent(
                receipt_id=acceptance.receipt_id,
                now=received_at,
            )
    elif intake.disposition is BetaSpaceDisposition.CONSENT_DECLINED:
        candidate_queued = await beta_space_store.enqueue_callback_delivery(
            receipt_id=acceptance.receipt_id,
            now=received_at,
        )
    return {
        "ok": True,
        "accepted": True,
        "duplicate": acceptance.duplicate,
        "space_processing": intake.disposition.value,
        "work_queued": (
            candidate_queued
            or intake.disposition
            in {
                BetaSpaceDisposition.CONSENT_ACCEPTED,
                BetaSpaceDisposition.CONSENT_DECLINED,
                BetaSpaceDisposition.FACT_ACCEPTED,
            }
        ),
    }


async def _drain_beta_checkpoint_dispatch(
    request: Request,
    settings: Settings,
) -> None:
    """Best-effort wake-up after durable acceptance; scheduled fallback remains."""

    if not settings.gate_beta_checkpoint_dispatch_enabled:
        return
    app_id = settings.gate_beta_checkpoint_github_app_id
    installation_id = settings.gate_beta_checkpoint_github_installation_id
    private_key = (
        settings.gate_beta_checkpoint_github_private_key.get_secret_value()
    )
    if not app_id or not installation_id or not private_key:
        logger.warning("Beta checkpoint dispatch disabled: configuration incomplete")
        return
    try:
        dispatcher = GitHubAppBetaCheckpointDispatcher(
            client=get_beta_model_http_client(request),
            app_id=app_id,
            installation_id=installation_id,
            private_key_pem=private_key,
        )
        result = await SessionFactoryBetaCheckpointDispatchDrain(
            request.app.state.database_runtime.session_factory,
            dispatcher,
        ).drain_once()
    except Exception as exc:
        logger.warning(
            "Beta checkpoint dispatch deferred: error_type=%s",
            exc.__class__.__name__,
        )
        return
    if result.disposition == "sent":
        logger.info(
            "Beta checkpoint dispatch sent: space=%s sequence=%s",
            result.space_id,
            result.accepted_sequence,
        )
    elif result.disposition == "deferred":
        logger.warning(
            "Beta checkpoint dispatch deferred: code=%s",
            result.error_code,
        )


class _BetaMaterialTelegram:
    """Send through the bound Beta bot and download one exact material."""

    def __init__(
        self,
        telegram: TelegramClient,
        downloader: TelegramMaterialDownloadAdapter,
    ) -> None:
        self._telegram = telegram
        self._downloader = downloader

    async def send_plain_message(self, *, chat_id: str, text: str) -> JsonObject:
        return await self._telegram.send_plain_message(chat_id=chat_id, text=text)

    async def download_file(self, *, file_id: str) -> bytes:
        return await self._downloader.download_file(file_id=file_id)


def _is_beta_material_message(update: JsonObject) -> bool:
    message = update.get("message")
    if not isinstance(message, dict):
        return False
    return (
        isinstance(message.get("voice"), dict)
        or isinstance(message.get("document"), dict)
        or isinstance(message.get("photo"), list)
    )


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
    if (
        type(update_id) is not int
        or update_id < 0
        or update_id > TELEGRAM_UPDATE_ID_MAX
    ):
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
        raise HTTPException(
            status_code=400, detail="Invalid Telegram receipt envelope"
        ) from None
    except Exception:
        raise HTTPException(
            status_code=503, detail="Durable Telegram receipt is unavailable"
        ) from None


async def _settle_beta_consent_callback(
    *,
    telegram: TelegramClient,
    update: JsonObject,
    disposition: BetaSpaceDisposition,
) -> str | None:
    if disposition not in {
        BetaSpaceDisposition.CONSENT_ACCEPTED,
        BetaSpaceDisposition.CONSENT_DECLINED,
    }:
        return None
    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return "unknown"
    callback_id = callback.get("id")
    message = callback.get("message")
    chat = message.get("chat") if isinstance(message, dict) else None
    message_id = message.get("message_id") if isinstance(message, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if (
        not isinstance(callback_id, str)
        or not callback_id
        or type(chat_id) is not int
        or type(message_id) is not int
    ):
        return "unknown"
    callback_text = (
        "Согласие принято."
        if disposition is BetaSpaceDisposition.CONSENT_ACCEPTED
        else "Бета не подключена."
    )
    try:
        await telegram.answer_callback_query(
            callback_query_id=callback_id,
            text=callback_text,
        )
        await telegram.edit_message_reply_markup(
            chat_id=str(chat_id),
            message_id=message_id,
        )
    except Exception:
        return "unknown"
    return "answered"


def _is_group_update(update: JsonObject) -> bool:
    update_kinds = {key for key in update if key != "update_id"}
    if update_kinds not in ({"message"}, {"my_chat_member"}, {"callback_query"}):
        return False
    message = update.get("message")
    if isinstance(message, dict):
        chat = message.get("chat")
        return isinstance(chat, dict) and chat.get("type") in {"group", "supergroup"}
    membership = update.get("my_chat_member")
    if isinstance(membership, dict):
        chat = membership.get("chat")
        return isinstance(chat, dict) and chat.get("type") in {"group", "supergroup"}
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        callback_message = callback.get("message")
        chat = (
            callback_message.get("chat") if isinstance(callback_message, dict) else None
        )
        return isinstance(chat, dict) and chat.get("type") in {"group", "supergroup"}
    return False


def _secrets_match(*, provided: str, configured: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode(), configured.encode())
    except UnicodeError:
        return False
