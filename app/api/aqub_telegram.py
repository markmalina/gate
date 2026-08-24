"""AQUB Telegram ingress and permanent live runtime forwarding."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_database_runtime, get_db_session
from app.application.aqub_food_media import AQUBFoodMediaQueue
from app.application.aqub_live import (
    AQUB_GATE_DELIVERY_PATH,
    AQUBLiveForwardResult,
    AQUBLiveIngressAdapter,
    HttpAQUBRuntimeClient,
)
from app.application.aqub_transport import (
    AQUB_PING_COMMAND,
    AQUB_TELEGRAM_WEBHOOK_PATH,
    AQUBDiagnosticPingAdapter,
    AQUBPhotoReference,
    AQUBTransportBinding,
    AQUBTransportContentKind,
    AQUBTransportInbound,
    AQUBTransportResult,
    AQUBTransportStore,
    AQUBVoiceReference,
)
from app.core.config import Settings, get_settings
from app.infrastructure.aqub_food_media import SqlAQUBFoodMediaStore
from app.infrastructure.aqub_general_route import select_aqub_inbound_binding
from app.infrastructure.aqub_installation import (
    SqlAQUBTransportReceiptStore,
    aqub_receipt_signing_key,
    load_aqub_production_binding,
)
from app.infrastructure.aqub_live import (
    AQUBLiveDeliveryError,
    AQUBLiveDeliveryService,
    ParsedAQUBDelivery,
    parse_aqub_delivery,
    verify_aqub_signature,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
JsonObject = dict[str, Any]
db_session_dependency = Depends(get_db_session)
_TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807
_TELEGRAM_MESSAGE_ID_MAX = (1 << 52) - 1
_PHOTO_CAPTION_MAX_CHARS = 1_024
_PHOTO_MESSAGE_REQUIRED_FIELDS = {"message_id", "from", "date", "chat", "photo"}
_PHOTO_MESSAGE_OPTIONAL_FIELDS = {"caption", "media_group_id"}
_PHOTO_SIZE_REQUIRED_FIELDS = {"file_id", "file_unique_id", "width", "height"}
_PHOTO_SIZE_OPTIONAL_FIELDS = {"file_size"}
_VOICE_MESSAGE_REQUIRED_FIELDS = {"message_id", "from", "date", "chat", "voice"}
_VOICE_MESSAGE_OPTIONAL_FIELDS: set[str] = set()
_VOICE_REQUIRED_FIELDS = {"file_id", "file_unique_id", "duration"}
_VOICE_OPTIONAL_FIELDS = {"mime_type", "file_size"}
_AUDIO_MESSAGE_REQUIRED_FIELDS = {"message_id", "from", "date", "chat", "audio"}
_AUDIO_MESSAGE_OPTIONAL_FIELDS: set[str] = set()
_AUDIO_REQUIRED_FIELDS = {"file_id", "file_unique_id", "duration", "mime_type"}
_AUDIO_OPTIONAL_FIELDS = {"file_size", "file_name"}


def get_aqub_transport_store(
    session: AsyncSession = db_session_dependency,
) -> AQUBTransportStore:
    return SqlAQUBTransportReceiptStore(session)


async def get_aqub_production_binding(
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> AQUBTransportBinding:
    """Resolve the installation binding from Gate persistence or fail closed."""

    try:
        return await load_aqub_production_binding(session, settings)
    except (RuntimeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB production binding is unavailable",
        ) from None


store_dependency = Depends(get_aqub_transport_store)
binding_dependency = Depends(get_aqub_production_binding)


async def get_aqub_food_media_queue(
    request: Request,
    settings: Settings = settings_dependency,
) -> AsyncIterator[AQUBFoodMediaQueue | None]:
    """Bind the durable queue only for the explicitly approved media mode."""

    if not settings.gate_aqub_food_media_processing_enabled:
        yield None
        return
    if not settings.gate_aqub_media_forward_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB media forwarding is disabled",
        )
    runtime = get_database_runtime(request)
    async with runtime.session_factory() as session:
        yield SqlAQUBFoodMediaStore(session)


media_queue_dependency = Depends(get_aqub_food_media_queue)


@router.post(AQUB_TELEGRAM_WEBHOOK_PATH)
async def aqub_telegram_ingress(
    request: Request,
    x_telegram_secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    store: AQUBTransportStore = store_dependency,
    binding: AQUBTransportBinding = binding_dependency,
    media_queue: AQUBFoodMediaQueue | None = media_queue_dependency,
) -> JsonObject:
    """Own the AQUB Telegram route from the durable installation binding."""

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

    binding = await select_aqub_inbound_binding(
        session,
        settings,
        food_binding=binding,
        chat_id=inbound.chat_id,
        actor_id=inbound.actor_id,
        content_kind=inbound.content_kind,
    )

    if inbound.command == AQUB_PING_COMMAND:
        result = await AQUBDiagnosticPingAdapter(
            binding=binding,
            store=store,
        ).handle(inbound)
        return _diagnostic_response(result, chat_id=inbound.chat_id)

    runtime_url = (
        os.getenv("GATE_AQUB_RUNTIME_URL", "").strip()
        if inbound.content_kind is AQUBTransportContentKind.TEXT
        or settings.gate_aqub_media_forward_enabled
        else ""
    )
    runtime = (
        HttpAQUBRuntimeClient(
            base_url=runtime_url,
            http_client=_process_http_client(request),
        )
        if runtime_url
        else None
    )
    try:
        live = await AQUBLiveIngressAdapter(
            binding=binding,
            store=store,
            runtime=runtime,
            media_queue=media_queue,
        ).handle(inbound)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc)[:160],
        ) from None
    delivered = await _deliver_live_messages(
        request=request,
        settings=settings,
        live=live,
        chat_id=inbound.chat_id,
    )
    if delivered:
        return {
            "ok": True,
            "owned": True,
            "accepted": True,
            "reason": live.reason,
            "duplicate": live.duplicate,
        }
    return _live_response(live, chat_id=inbound.chat_id)


@router.post(AQUB_GATE_DELIVERY_PATH)
async def aqub_live_delivery(
    request: Request,
    x_aqub_signature: Annotated[str | None, Header(alias="X-AQUB-Signature")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    binding: AQUBTransportBinding = binding_dependency,
) -> JsonObject:
    """Accept one signed AQUB delivery command and settle it through Telegram."""

    try:
        signing_key = aqub_receipt_signing_key(settings)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB signing key is unavailable",
        ) from None
    if x_aqub_signature is None:
        raise HTTPException(status_code=401, detail="AQUB signature is required")
    raw_body = await request.body()
    try:
        verify_aqub_signature(
            raw_body=raw_body,
            signature=x_aqub_signature,
            signing_key=signing_key,
        )
        command = parse_aqub_delivery(
            raw_body,
            signing_key=signing_key,
            raw_chat_id=binding.group_chat_id,
        )
    except AQUBLiveDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    token = settings.telegram_aqub_bot_token.get_secret_value()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB Telegram token is unavailable",
        )
    telegram = TelegramClient(
        token,
        http_client=_process_http_client(request),
    )
    try:
        return await AQUBLiveDeliveryService(
            session=session,
            telegram=telegram,
            signing_key=signing_key,
            raw_chat_id=binding.group_chat_id,
        ).deliver(command)
    except AQUBLiveDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


def _process_http_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http_client", None)
    if not isinstance(client, httpx.AsyncClient):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gate HTTP transport is unavailable",
        )
    return client


def _project_inbound(
    update: JsonObject,
    *,
    bot_username: str,
    received_at: datetime,
) -> AQUBTransportInbound | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if (
        type(update_id) is not int
        or update_id < 0
        or update_id > _TELEGRAM_UPDATE_ID_MAX
        or not isinstance(message, dict)
    ):
        return None
    if set(update) != {"update_id", "message"}:
        return None
    chat = message.get("chat")
    actor = message.get("from")
    occurred_epoch = message.get("date")
    message_id = message.get("message_id")
    if (
        not isinstance(chat, dict)
        or not isinstance(actor, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or type(chat.get("id")) is not int
        or type(actor.get("id")) is not int
        or actor.get("is_bot") is not False
        or type(occurred_epoch) is not int
        or type(message_id) is not int
        or message_id < 1
        or message_id > _TELEGRAM_MESSAGE_ID_MAX
    ):
        return None
    text = message.get("text")
    photo: AQUBPhotoReference | None = None
    voice: AQUBVoiceReference | None = None
    if "text" in message:
        if not isinstance(text, str) or any(
            media_kind in message for media_kind in ("photo", "voice", "audio")
        ):
            return None
        content_kind = AQUBTransportContentKind.TEXT
        command: str | None = text
    elif "photo" in message:
        photo = _project_photo(message)
        if photo is None:
            return None
        content_kind = AQUBTransportContentKind.PHOTO
        command = None
    elif "voice" in message:
        voice = _project_voice(message)
        if voice is None:
            return None
        content_kind = AQUBTransportContentKind.VOICE
        command = None
    elif "audio" in message:
        voice = _project_audio(message)
        if voice is None:
            return None
        content_kind = AQUBTransportContentKind.VOICE
        command = None
    else:
        return None
    try:
        occurred_at = datetime.fromtimestamp(occurred_epoch, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return AQUBTransportInbound(
        bot_username=bot_username,
        external_id=str(update_id),
        chat_id=str(chat["id"]),
        actor_id=str(actor["id"]),
        command=command,
        payload=update,
        occurred_at=occurred_at,
        received_at=received_at,
        content_kind=content_kind,
        photo=photo,
        voice=voice,
        media_group_id=(
            cast(str, message.get("media_group_id"))
            if content_kind is AQUBTransportContentKind.PHOTO
            and isinstance(message.get("media_group_id"), str)
            else None
        ),
    )


def _project_photo(message: JsonObject) -> AQUBPhotoReference | None:
    message_fields = set(message)
    if not _PHOTO_MESSAGE_REQUIRED_FIELDS.issubset(message_fields) or not message_fields.issubset(
        _PHOTO_MESSAGE_REQUIRED_FIELDS | _PHOTO_MESSAGE_OPTIONAL_FIELDS
    ):
        return None
    caption = message.get("caption")
    if "caption" in message and (
        not isinstance(caption, str) or len(caption) > _PHOTO_CAPTION_MAX_CHARS
    ):
        return None
    media_group_id = message.get("media_group_id")
    if "media_group_id" in message and (
        not isinstance(media_group_id, str)
        or not media_group_id
        or media_group_id != media_group_id.strip()
        or len(media_group_id) > 128
    ):
        return None
    values = message.get("photo")
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        return None
    references: list[AQUBPhotoReference] = []
    for value in values:
        if not isinstance(value, dict):
            return None
        photo_size_fields = set(value)
        if not _PHOTO_SIZE_REQUIRED_FIELDS.issubset(
            photo_size_fields
        ) or not photo_size_fields.issubset(
            _PHOTO_SIZE_REQUIRED_FIELDS | _PHOTO_SIZE_OPTIONAL_FIELDS
        ):
            return None
        file_id = value.get("file_id")
        file_unique_id = value.get("file_unique_id")
        width = value.get("width")
        height = value.get("height")
        file_size = value.get("file_size")
        if (
            not isinstance(file_id, str)
            or not isinstance(file_unique_id, str)
            or type(width) is not int
            or type(height) is not int
            or ("file_size" in value and type(file_size) is not int)
        ):
            return None
        try:
            references.append(
                AQUBPhotoReference(
                    file_id=file_id,
                    file_unique_id=file_unique_id,
                    width=width,
                    height=height,
                    file_size=file_size,
                )
            )
        except ValueError:
            return None
    return max(
        references,
        key=lambda item: (item.width * item.height, item.file_size or 0),
    )


def _project_audio(message: JsonObject) -> AQUBVoiceReference | None:
    """Normalize one strict Telegram OGG audio upload into the durable voice path."""

    message_fields = set(message)
    if not _AUDIO_MESSAGE_REQUIRED_FIELDS.issubset(message_fields) or not message_fields.issubset(
        _AUDIO_MESSAGE_REQUIRED_FIELDS | _AUDIO_MESSAGE_OPTIONAL_FIELDS
    ):
        return None
    value = message.get("audio")
    if not isinstance(value, dict):
        return None
    fields = set(value)
    if not _AUDIO_REQUIRED_FIELDS.issubset(fields) or not fields.issubset(
        _AUDIO_REQUIRED_FIELDS | _AUDIO_OPTIONAL_FIELDS
    ):
        return None
    file_id = value.get("file_id")
    file_unique_id = value.get("file_unique_id")
    duration = value.get("duration")
    mime_type = value.get("mime_type")
    file_size = value.get("file_size")
    file_name = value.get("file_name")
    if (
        not isinstance(file_id, str)
        or not isinstance(file_unique_id, str)
        or type(duration) is not int
        or mime_type != "audio/ogg"
        or ("file_size" in value and type(file_size) is not int)
        or (
            "file_name" in value
            and (
                not isinstance(file_name, str)
                or len(file_name) > 255
                or not file_name.lower().endswith(".ogg")
            )
        )
    ):
        return None
    try:
        return AQUBVoiceReference(
            file_id=file_id,
            file_unique_id=file_unique_id,
            duration=duration,
            mime_type=mime_type,
            file_size=file_size,
        )
    except (TypeError, ValueError):
        return None


def _project_voice(message: JsonObject) -> AQUBVoiceReference | None:
    message_fields = set(message)
    if not _VOICE_MESSAGE_REQUIRED_FIELDS.issubset(message_fields) or not message_fields.issubset(
        _VOICE_MESSAGE_REQUIRED_FIELDS | _VOICE_MESSAGE_OPTIONAL_FIELDS
    ):
        return None
    value = message.get("voice")
    if not isinstance(value, dict):
        return None
    fields = set(value)
    if not _VOICE_REQUIRED_FIELDS.issubset(fields) or not fields.issubset(
        _VOICE_REQUIRED_FIELDS | _VOICE_OPTIONAL_FIELDS
    ):
        return None
    file_id = value.get("file_id")
    file_unique_id = value.get("file_unique_id")
    duration = value.get("duration")
    mime_type = value.get("mime_type")
    file_size = value.get("file_size")
    if (
        not isinstance(file_id, str)
        or not isinstance(file_unique_id, str)
        or type(duration) is not int
        or ("mime_type" in value and not isinstance(mime_type, str))
        or ("file_size" in value and type(file_size) is not int)
    ):
        return None
    try:
        return AQUBVoiceReference(
            file_id=file_id,
            file_unique_id=file_unique_id,
            duration=duration,
            mime_type=mime_type,
            file_size=file_size,
        )
    except (TypeError, ValueError):
        return None


def _diagnostic_response(result: AQUBTransportResult, *, chat_id: str) -> JsonObject:
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
    }


def _live_response(result: AQUBLiveForwardResult, *, chat_id: str) -> JsonObject:
    if not result.accepted:
        return _unowned_response(reason=result.reason)
    if result.duplicate and result.downstream is None:
        return {
            "ok": True,
            "owned": True,
            "accepted": True,
            "reason": result.reason,
            "duplicate": True,
        }
    downstream = result.downstream or {}
    domain = downstream.get("result")
    if isinstance(domain, dict) and domain.get("suppress_reply") is True:
        return {
            "ok": True,
            "owned": True,
            "accepted": True,
            "reason": result.reason,
            "duplicate": result.duplicate,
        }
    reply_text = _domain_last_reply(domain)
    if isinstance(domain, dict) and reply_text is not None:
        response: JsonObject = {
            "method": "sendMessage",
            "chat_id": int(chat_id),
            "text": reply_text,
        }
        if domain.get("force_reply") is True:
            response["reply_markup"] = {"force_reply": True, "selective": True}
        return response
    if result.captured_content is AQUBTransportContentKind.PHOTO:
        return {
            "method": "sendMessage",
            "chat_id": int(chat_id),
            "text": "Фото принято.",
        }
    if result.captured_content is AQUBTransportContentKind.TEXT:
        return {
            "method": "sendMessage",
            "chat_id": int(chat_id),
            "text": (
                "Сообщение сохранил, но сейчас не смог разобрать питание. Попробуйте ещё раз."
            ),
        }
    if result.captured_content is AQUBTransportContentKind.VOICE:
        return {
            "method": "sendMessage",
            "chat_id": int(chat_id),
            "text": "Голосовое принято.",
        }
    return {
        "ok": True,
        "owned": True,
        "accepted": True,
        "reason": result.reason,
        "duplicate": result.duplicate,
    }


def _domain_last_reply(domain: object) -> str | None:
    if not isinstance(domain, dict):
        return None
    messages = domain.get("reply_messages")
    if isinstance(messages, list) and messages:
        value = messages[-1]
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 4_096:
            return value.strip()
        return None
    value = domain.get("reply_text")
    return (
        value.strip()
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 4_096
        else None
    )


async def _deliver_live_messages(
    *,
    request: Request,
    settings: Settings,
    live: AQUBLiveForwardResult,
    chat_id: str,
) -> bool:
    if not isinstance(live.downstream, dict):
        return False
    domain = live.downstream.get("result")
    if not isinstance(domain, dict):
        return False
    values = _domain_reply_messages(domain)
    if values is None:
        return False
    if live.delivery_ref is None:
        raise HTTPException(status_code=502, detail="AQUB delivery reference is missing")
    if not 1 <= len(values) <= 5:
        raise HTTPException(status_code=502, detail="AQUB reply messages are invalid")
    token = settings.telegram_aqub_bot_token.get_secret_value()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB Telegram token is unavailable",
        )
    try:
        signing_key = aqub_receipt_signing_key(settings)
        runtime = get_database_runtime(request)
    except (RuntimeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB durable delivery is unavailable",
        ) from None

    telegram = TelegramClient(token, http_client=_process_http_client(request))
    async with runtime.session_factory() as session:
        service = AQUBLiveDeliveryService(
            session=session,
            telegram=telegram,
            signing_key=signing_key,
            raw_chat_id=chat_id,
        )
        for index, text_value in enumerate(values):
            idempotency_key = f"aqub-live-reply:{live.delivery_ref}:{index}"
            force_reply = domain.get("force_reply") is True and index == len(values) - 1
            request_value = {
                "idempotency_key": idempotency_key,
                "chat_ref": _live_opaque_ref(signing_key, "chat", chat_id),
                "assignment_ref": live.delivery_ref,
                "step_ref": _live_opaque_ref(signing_key, "reply", str(index)),
                "text": text_value,
                "force_reply": force_reply,
            }
            command = ParsedAQUBDelivery(
                command_id=uuid5(NAMESPACE_URL, idempotency_key),
                idempotency_key=idempotency_key,
                chat_ref=cast(str, request_value["chat_ref"]),
                assignment_ref=live.delivery_ref,
                step_ref=cast(str, request_value["step_ref"]),
                text=text_value,
                request_sha256=hashlib.sha256(
                    json.dumps(
                        request_value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                force_reply=force_reply,
            )
            try:
                result = await service.deliver(command)
            except AQUBLiveDeliveryError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
            terminal = result.get("terminal")
            if not isinstance(terminal, dict) or terminal.get("status") != "sent":
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="AQUB Telegram delivery is not terminal",
                )
    return True


def _domain_reply_messages(domain: JsonObject) -> list[str] | None:
    messages = domain.get("reply_messages")
    if isinstance(messages, list):
        if not 1 <= len(messages) <= 5 or any(
            not isinstance(value, str) or not value.strip() or len(value.strip()) > 4_096
            for value in messages
        ):
            raise HTTPException(status_code=502, detail="AQUB reply messages are invalid")
        return [cast(str, value).strip() for value in messages]
    reply_text = domain.get("reply_text")
    if isinstance(reply_text, str) and reply_text.strip() and len(reply_text.strip()) <= 4_096:
        return [reply_text.strip()]
    return None


def _live_opaque_ref(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(
        signing_key,
        f"{kind}:{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{kind}:{digest}"


def _unowned_response(*, reason: str = "aqub_route_not_owned") -> JsonObject:
    return {
        "ok": True,
        "owned": False,
        "accepted": False,
        "reason": reason,
    }


def _secrets_match(*, provided: str, configured: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode(), configured.encode())
    except UnicodeError:
        return False
