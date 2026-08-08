"""AQUB Telegram ingress: preflight ping plus isolated live runtime forwarding."""

from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.application.aqub_live import (
    AQUB_GATE_DELIVERY_PATH,
    AQUBLiveForwardResult,
    AQUBLiveIngressAdapter,
    HttpAQUBRuntimeClient,
)
from app.application.aqub_s0_preflight import (
    AQUB_BOT_USERNAME,
    AQUB_OWNED_PATH,
    AQUB_PING_COMMAND,
    AQUBPreflightInbound,
    AQUBPreflightResult,
    AQUBPreflightStore,
    AQUBS0PreflightAdapter,
)
from app.core.config import Settings, get_settings
from app.infrastructure.aqub_live import (
    AQUBLiveDeliveryError,
    AQUBLiveDeliveryService,
    parse_aqub_delivery,
    verify_aqub_signature,
)
from app.infrastructure.aqub_s0_preflight import (
    SqlAQUBPreflightReceiptStore,
    build_aqub_s0_binding,
)
from app.services.telegram import TelegramClient, build_telegram_http_client

router = APIRouter()
settings_dependency = Depends(get_settings)
JsonObject = dict[str, Any]
db_session_dependency = Depends(get_db_session)


def get_aqub_preflight_store(
    session: AsyncSession = db_session_dependency,
) -> AQUBPreflightStore:
    return SqlAQUBPreflightReceiptStore(session)


store_dependency = Depends(get_aqub_preflight_store)


@router.post(AQUB_OWNED_PATH)
async def aqub_telegram_preflight(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    store: AQUBPreflightStore = store_dependency,
) -> JsonObject:
    """Own the exact AQUB route; keep ping semantics and optionally forward live input."""

    configured_secret = settings.telegram_aqub_webhook_secret.get_secret_value()
    if not configured_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB webhook secret is unavailable",
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
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from None
    if not isinstance(raw_update, dict):
        raise HTTPException(status_code=400, detail="Invalid Telegram update")

    received_at = datetime.now(UTC)
    projected = _project_inbound(
        cast(JsonObject, raw_update),
        bot_username=AQUB_BOT_USERNAME,
        received_at=received_at,
    )

    try:
        binding = build_aqub_s0_binding(settings)
    except ValueError:
        if _is_bootstrap_ping(projected):
            return _bootstrap_response(projected)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB preflight binding is incomplete",
        ) from None

    if not binding.enabled:
        if _is_bootstrap_ping(projected):
            return _bootstrap_response(projected)
        return _unowned_response(reason="aqub_bootstrap_waiting")

    inbound = _project_inbound(
        cast(JsonObject, raw_update),
        bot_username=binding.bot_username,
        received_at=received_at,
    )
    if inbound is None:
        return _unowned_response()

    if inbound.command == AQUB_PING_COMMAND or not _env_enabled("GATE_AQUB_LIVE_ENABLED"):
        result = await AQUBS0PreflightAdapter(
            binding=binding,
            store=store,
        ).handle(inbound)
        return _response(result, chat_id=inbound.chat_id)

    runtime_url = os.getenv("GATE_AQUB_RUNTIME_URL", "").strip()
    if not runtime_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB runtime URL is unavailable",
        )
    try:
        live = await AQUBLiveIngressAdapter(
            binding=binding,
            store=store,
            runtime=HttpAQUBRuntimeClient(base_url=runtime_url),
        ).handle(inbound)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc)[:160],
        ) from None
    return _live_response(live, chat_id=inbound.chat_id)


@router.post(AQUB_GATE_DELIVERY_PATH)
async def aqub_live_delivery(
    request: Request,
    x_aqub_signature: Annotated[str | None, Header(alias="X-AQUB-Signature")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> JsonObject:
    """Accept one signed AQUB delivery command and settle it through Telegram."""

    if not _env_enabled("GATE_AQUB_DELIVERY_ENABLED"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB live delivery is disabled",
        )
    signing_key = settings.gate_aqub_receipt_signing_key.get_secret_value().encode()
    if len(signing_key) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB signing key is unavailable",
        )
    if x_aqub_signature is None:
        raise HTTPException(status_code=401, detail="AQUB signature is required")
    raw_body = await request.body()
    try:
        verify_aqub_signature(
            raw_body=raw_body,
            signature=x_aqub_signature,
            signing_key=signing_key,
        )
        command = parse_aqub_delivery(raw_body, signing_key=signing_key)
    except AQUBLiveDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    token = settings.telegram_aqub_bot_token.get_secret_value()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB Telegram token is unavailable",
        )
    try:
        async with build_telegram_http_client() as http_client:
            telegram = TelegramClient(token, http_client=http_client)
            return await AQUBLiveDeliveryService(
                session=session,
                telegram=telegram,
                signing_key=signing_key,
            ).deliver(command)
    except AQUBLiveDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


def _project_inbound(
    update: JsonObject,
    *,
    bot_username: str,
    received_at: datetime,
) -> AQUBPreflightInbound | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if type(update_id) is not int or update_id < 0 or not isinstance(message, dict):
        return None
    if set(update) != {"update_id", "message"}:
        return None
    chat = message.get("chat")
    actor = message.get("from")
    text = message.get("text")
    occurred_epoch = message.get("date")
    if (
        not isinstance(chat, dict)
        or not isinstance(actor, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or type(chat.get("id")) is not int
        or type(actor.get("id")) is not int
        or actor.get("is_bot") is not False
        or not isinstance(text, str)
        or type(occurred_epoch) is not int
    ):
        return None
    try:
        occurred_at = datetime.fromtimestamp(occurred_epoch, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return AQUBPreflightInbound(
        bot_username=bot_username,
        external_id=str(update_id),
        chat_id=str(chat["id"]),
        actor_id=str(actor["id"]),
        command=text,
        payload=update,
        occurred_at=occurred_at,
        received_at=received_at,
    )


def _is_bootstrap_ping(inbound: AQUBPreflightInbound | None) -> bool:
    return inbound is not None and inbound.command == AQUB_PING_COMMAND


def _bootstrap_response(inbound: AQUBPreflightInbound | None) -> JsonObject:
    if inbound is None:
        return _unowned_response(reason="aqub_bootstrap_invalid")
    return {
        "method": "sendMessage",
        "chat_id": int(inbound.chat_id),
        "text": (
            "AQUB bootstrap принят. Скопируйте эту строку в ChatGPT:\n"
            f"AQUB_BIND chat_id={inbound.chat_id} alpha_user_id={inbound.actor_id}"
        ),
    }


def _response(result: AQUBPreflightResult, *, chat_id: str) -> JsonObject:
    if result.accepted and result.reply_text is not None and result.receipt is not None:
        receipt = result.receipt
        duplicate = "true" if receipt.duplicate else "false"
        return {
            "method": "sendMessage",
            "chat_id": int(chat_id),
            "text": (
                f"{result.reply_text}\n"
                "Скопируйте receipt в ChatGPT:\n"
                "AQUB_RECEIPT "
                f"status={receipt.status} "
                f"role={receipt.role} "
                f"duplicate={duplicate} "
                f"receipt_ref={receipt.command_receipt_ref} "
                f"root_ref={receipt.root_event_ref} "
                f"chat_ref={receipt.chat_ref} "
                f"actor_ref={receipt.actor_ref} "
                f"signature={receipt.signature}"
            ),
        }

    return {
        "ok": True,
        "owned": result.owned,
        "accepted": result.accepted,
        "reason": result.reason,
        "legacy_dispatch": result.legacy_dispatch,
        "canary_dispatch": result.canary_dispatch,
    }


def _live_response(result: AQUBLiveForwardResult, *, chat_id: str) -> JsonObject:
    if not result.accepted:
        return _unowned_response(reason=result.reason)
    downstream = result.downstream or {}
    domain = downstream.get("result")
    if isinstance(domain, dict) and isinstance(domain.get("reply_text"), str):
        response: JsonObject = {
            "method": "sendMessage",
            "chat_id": int(chat_id),
            "text": domain["reply_text"],
        }
        if domain.get("force_reply") is True:
            response["reply_markup"] = {"force_reply": True, "selective": True}
        return response
    return {
        "ok": True,
        "owned": True,
        "accepted": True,
        "reason": result.reason,
        "duplicate": result.duplicate,
        "legacy_dispatch": False,
        "canary_dispatch": False,
    }


def _unowned_response(*, reason: str = "aqub_route_not_owned") -> JsonObject:
    return {
        "ok": True,
        "owned": False,
        "accepted": False,
        "reason": reason,
        "legacy_dispatch": None,
        "canary_dispatch": None,
    }


def _secrets_match(*, provided: str, configured: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode(), configured.encode())
    except UnicodeError:
        return False


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
