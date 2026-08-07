"""Separate authenticated durable ingress for the Beta group bot."""

from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import httpx
from app.api.deps import get_db_session
from app.api.telegram_ingress import get_telegram_receipt_store
from app.application.beta_space_intake import (
    BetaSpaceDisposition,
    DeliverBetaSpaceConsent,
    ProcessBetaSpaceUpdate,
)
from app.application.beta_wedding_reply import (
    BetaWeddingContextMessage,
    BetaWeddingReplyModel,
    RespondToBetaWeddingMessage,
)
from app.application.beta_wedding_voice import ProcessBetaWeddingVoice
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
    prepare_beta_katya_candidate_binding,
)
from app.infrastructure.beta_space import (
    SqlBetaSpaceStore,
    beta_space_production_ready,
)
from app.infrastructure.beta_wedding_binding import (
    BETA_WEDDING_TENANT_REF,
    BetaWeddingBindingError,
    extract_beta_wedding_activation_candidate,
    extract_beta_wedding_source_candidate,
    prepare_beta_wedding_binding,
)
from app.infrastructure.beta_wedding_reply import (
    BetaWeddingModelError,
    OpenAIResponsesBetaWeddingModel,
    SqlBetaWeddingReplyStore,
)
from app.infrastructure.beta_wedding_voice import SqlBetaWeddingVoiceStore
from app.infrastructure.beta_wedding_voice_provider import (
    OpenAIBetaWeddingVoiceStructurer,
    OpenAIBetaWeddingVoiceTranscriber,
    TelegramVoiceDownloadAdapter,
)
from app.services.telegram import TelegramClient
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

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
    wedding_source_candidate = extract_beta_wedding_source_candidate(update)
    if wedding_source_candidate is not None:
        try:
            await prepare_beta_wedding_binding(
                session,
                workspace_key=settings.gate_workspace_id,
                now=received_at,
                candidate=wedding_source_candidate,
            )
        except BetaWeddingBindingError as exc:
            return {
                "ok": True,
                "accepted": True,
                "duplicate": acceptance.duplicate,
                "ignored": True,
                "reason": exc.code,
            }
    wedding_candidate = extract_beta_wedding_activation_candidate(update)
    if wedding_candidate is not None:
        try:
            binding_result = await prepare_beta_wedding_binding(
                session,
                workspace_key=settings.gate_workspace_id,
                now=received_at,
                candidate=wedding_candidate,
            )
        except BetaWeddingBindingError as exc:
            return {
                "ok": True,
                "accepted": True,
                "duplicate": acceptance.duplicate,
                "ignored": True,
                "reason": exc.code,
            }
        return {
            "ok": True,
            "accepted": True,
            "duplicate": acceptance.duplicate,
            "ignored": True,
            "reason": f"beta_wedding_binding_{binding_result}",
        }
    beta_telegram = None
    callback_owner = extract_beta_callback_owner_candidate(update)
    if callback_owner is not None:
        beta_telegram = get_beta_telegram_client(request)
        member_response = await beta_telegram.get_chat_member(
            chat_id=callback_owner.telegram_chat_id,
            user_id=callback_owner.telegram_user_id,
        )
        member = member_response.get("result")
        member_user = member.get("user") if isinstance(member, dict) else None
        member_user_id = (
            member_user.get("id") if isinstance(member_user, dict) else None
        )
        if (
            not isinstance(member, dict)
            or member.get("status") != "creator"
            or not isinstance(member_user, dict)
            or member_user.get("is_bot") is not False
            or type(member_user_id) is not int
            or str(member_user_id) != callback_owner.telegram_user_id
        ):
            return {
                "ok": True,
                "accepted": True,
                "duplicate": acceptance.duplicate,
                "ignored": True,
                "reason": "callback_owner_not_exact",
            }
        await prepare_beta_katya_candidate_binding(
            session,
            workspace_key=settings.gate_workspace_id,
            now=received_at,
            telegram_chat_id=callback_owner.telegram_chat_id,
            telegram_user_id=callback_owner.telegram_user_id,
        )
    intake = await ProcessBetaSpaceUpdate(
        store=beta_space_store,
        production_ready=beta_space_production_ready(settings),
    ).execute(
        update=update,
        receipt_id=acceptance.receipt_id,
        received_at=received_at,
    )
    callback_delivery = None
    if intake.disposition in {
        BetaSpaceDisposition.CONSENT_ACCEPTED,
        BetaSpaceDisposition.CONSENT_DECLINED,
    }:
        beta_telegram = get_beta_telegram_client(request)
        callback_delivery = await _settle_beta_consent_callback(
            telegram=beta_telegram,
            update=update,
            disposition=intake.disposition,
        )
    delivery = None
    if intake.delivery_id is not None:
        if beta_telegram is None:
            beta_telegram = get_beta_telegram_client(request)
        delivery = await DeliverBetaSpaceConsent(
            store=beta_space_store,
            telegram=beta_telegram,
            enabled=beta_space_production_ready(settings),
        ).execute(delivery_id=intake.delivery_id, now=received_at)

    wedding_reply = None
    if (
        intake.disposition is BetaSpaceDisposition.FACT_ACCEPTED
        and intake.tenant_ref == BETA_WEDDING_TENANT_REF
    ):
        if beta_telegram is None:
            beta_telegram = get_beta_telegram_client(request)
        http_client = get_beta_model_http_client(request)
        if _is_wedding_voice_message(update):
            downloader = TelegramVoiceDownloadAdapter(
                credential=settings.telegram_beta_bot_token.get_secret_value(),
                api_base_url=os.getenv(
                    "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
                ),
                client=http_client,
            )
            voice_result = await ProcessBetaWeddingVoice(
                store=SqlBetaWeddingVoiceStore(session),
                telegram=_WeddingVoiceTelegram(beta_telegram, downloader),
                transcriber=OpenAIBetaWeddingVoiceTranscriber(
                    api_key=settings.openai_api_key.get_secret_value(),
                    api_base_url=settings.openai_api_base_url,
                    client=http_client,
                ),
                structurer=OpenAIBetaWeddingVoiceStructurer(
                    api_key=settings.openai_api_key.get_secret_value(),
                    api_base_url=settings.openai_api_base_url,
                    model=settings.gate_beta_model_profile,
                    client=http_client,
                ),
            ).execute(receipt_id=acceptance.receipt_id, now=received_at)
            wedding_reply = voice_result.disposition.value
        else:
            try:
                model: BetaWeddingReplyModel = OpenAIResponsesBetaWeddingModel(
                    http_client=http_client,
                    api_key=settings.openai_api_key.get_secret_value(),
                    api_base_url=settings.openai_api_base_url,
                    model=settings.gate_beta_model_profile,
                    max_output_tokens=settings.gate_beta_model_max_output_tokens,
                    timeout_seconds=settings.gate_beta_model_timeout_seconds,
                )
            except BetaWeddingModelError:
                model = _UnavailableBetaWeddingModel()
            reply_result = await RespondToBetaWeddingMessage(
                store=SqlBetaWeddingReplyStore(session),
                model=model,
                telegram=beta_telegram,
            ).execute(
                receipt_id=acceptance.receipt_id,
                now=received_at,
            )
            wedding_reply = reply_result.disposition.value
    return {
        "ok": True,
        "accepted": True,
        "duplicate": acceptance.duplicate,
        "space_processing": intake.disposition.value,
        "callback_delivery": callback_delivery,
        "delivery": delivery,
        "wedding_reply": wedding_reply,
    }


class _WeddingVoiceTelegram:
    """Small MVP adapter: send through the bound Beta bot, download its voice file."""

    def __init__(
        self, telegram: TelegramClient, downloader: TelegramVoiceDownloadAdapter
    ) -> None:
        self._telegram = telegram
        self._downloader = downloader

    async def send_plain_message(self, *, chat_id: str, text: str) -> JsonObject:
        return await self._telegram.send_plain_message(chat_id=chat_id, text=text)

    async def download_voice(self, *, file_id: str) -> bytes:
        return await self._downloader.download_voice(file_id=file_id)


def _is_wedding_voice_message(update: JsonObject) -> bool:
    message = update.get("message")
    return isinstance(message, dict) and isinstance(message.get("voice"), dict)


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
