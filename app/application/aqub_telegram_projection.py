"""One fail-closed Telegram projection shared by AQUB webhook and archive jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.aqub_transport import (
    AQUBPhotoReference,
    AQUBTransportContentKind,
    AQUBTransportInbound,
)

JsonObject = dict[str, Any]

_TELEGRAM_UPDATE_ID_MAX = 9_223_372_036_854_775_807
_TELEGRAM_MESSAGE_ID_MAX = (1 << 52) - 1
_PHOTO_CAPTION_MAX_CHARS = 1_024
_PHOTO_MESSAGE_REQUIRED_FIELDS = {"message_id", "from", "date", "chat", "photo"}
_PHOTO_MESSAGE_OPTIONAL_FIELDS = {"caption"}
_PHOTO_SIZE_REQUIRED_FIELDS = {"file_id", "file_unique_id", "width", "height"}
_PHOTO_SIZE_OPTIONAL_FIELDS = {"file_size"}


def project_aqub_telegram_inbound(
    update: JsonObject,
    *,
    bot_username: str,
    received_at: datetime,
) -> AQUBTransportInbound | None:
    """Project only the existing text shape or the strict owner-photo shape.

    Archive discovery and claim deliberately call this exact function again so
    they cannot accept provider context that the webhook would reject.
    """

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
    if "text" in message:
        if not isinstance(text, str) or "photo" in message:
            return None
        content_kind = AQUBTransportContentKind.TEXT
        command: str | None = text
    else:
        photo = project_aqub_photo(message)
        if photo is None:
            return None
        content_kind = AQUBTransportContentKind.PHOTO
        command = None
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
    )


def project_aqub_photo(message: JsonObject) -> AQUBPhotoReference | None:
    """Validate one top-level Telegram Message photo with a closed field set."""

    message_fields = set(message)
    if (
        not _PHOTO_MESSAGE_REQUIRED_FIELDS.issubset(message_fields)
        or not message_fields.issubset(
            _PHOTO_MESSAGE_REQUIRED_FIELDS | _PHOTO_MESSAGE_OPTIONAL_FIELDS
        )
    ):
        return None
    caption = message.get("caption")
    if "caption" in message and (
        not isinstance(caption, str) or len(caption) > _PHOTO_CAPTION_MAX_CHARS
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
        if (
            not _PHOTO_SIZE_REQUIRED_FIELDS.issubset(photo_size_fields)
            or not photo_size_fields.issubset(
                _PHOTO_SIZE_REQUIRED_FIELDS | _PHOTO_SIZE_OPTIONAL_FIELDS
            )
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
