"""Default-off HTTP ingress for the AQUB Sprint 0 Telegram preflight."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.application.aqub_s0_preflight import (
    AQUB_OWNED_PATH,
    AQUBPreflightInbound,
    AQUBPreflightResult,
    AQUBPreflightStore,
    AQUBS0PreflightAdapter,
)
from app.core.config import Settings, get_settings
from app.infrastructure.aqub_s0_preflight import (
    SqlAQUBPreflightReceiptStore,
    build_aqub_s0_binding,
)

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
    """Own one exact AQUB ping before any legacy/canary processing exists."""

    try:
        binding = build_aqub_s0_binding(settings)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB preflight binding is incomplete",
        ) from None
    if not binding.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB preflight is disabled",
        )
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
    inbound = _project_inbound(
        cast(JsonObject, raw_update),
        bot_username=binding.bot_username,
        received_at=received_at,
    )
    if inbound is None:
        return _unowned_response()

    result = await AQUBS0PreflightAdapter(
        binding=binding,
        store=store,
    ).handle(inbound)
    return _response(result)


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


def _response(result: AQUBPreflightResult) -> JsonObject:
    response: JsonObject = {
        "ok": True,
        "owned": result.owned,
        "accepted": result.accepted,
        "reason": result.reason,
        "legacy_dispatch": result.legacy_dispatch,
        "canary_dispatch": result.canary_dispatch,
    }
    if result.reply_text is not None:
        response["reply"] = result.reply_text
    if result.receipt is not None:
        response["receipt"] = {
            **result.receipt.unsigned_dict(),
            "signature": result.receipt.signature,
        }
    return response


def _unowned_response() -> JsonObject:
    return {
        "ok": True,
        "owned": False,
        "accepted": False,
        "reason": "aqub_route_not_owned",
        "legacy_dispatch": None,
        "canary_dispatch": None,
    }


def _secrets_match(*, provided: str, configured: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode(), configured.encode())
    except UnicodeError:
        return False
