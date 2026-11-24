"""Scoped MCP readback for Telegram reply attachments."""

import base64
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mcp_results import (
    jsonrpc_result as _jsonrpc_result,
)
from app.api.mcp_results import (
    tool_error_result as _tool_error_result,
)
from app.application.telegram_attachments import (
    TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES,
    TelegramAttachmentError,
    TelegramAttachmentReference,
    extract_telegram_attachments,
    validated_telegram_receipt_payload,
    validated_telegram_sent_document_payload,
)
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.models.canonical import CanonicalMessage
from app.models.domain import (
    GateEvent,
    Message,
    MessageDirection,
    TelegramReceipt,
    TelegramUpdate,
)

JsonObject = dict[str, Any]


async def _attachment_reply_context_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    message_id: UUID,
    attachment_index: int,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    visible_message_ids = {
        item.get("id")
        for item in context.get("messages", [])
        if isinstance(item, dict)
    }
    if str(message_id) not in visible_message_ids:
        return _tool_error_result(
            request_id=request_id,
            code="attachment_message_not_in_context",
            message="Selected message is not in the scoped reply context",
        )

    conversation = context["conversation"]
    result = await session.execute(
        select(Message, TelegramUpdate, GateEvent, TelegramReceipt)
        .outerjoin(
            TelegramUpdate,
            (
                (TelegramUpdate.bot_id == Message.bot_id)
                & (TelegramUpdate.update_id == Message.telegram_update_id)
            ),
        )
        .outerjoin(GateEvent, GateEvent.id == Message.gate_event_id)
        .outerjoin(
            CanonicalMessage,
            CanonicalMessage.id == Message.canonical_message_id,
        )
        .outerjoin(
            TelegramReceipt,
            (
                (TelegramReceipt.id == CanonicalMessage.receipt_id)
                & (TelegramReceipt.bot_key == "telegram_business")
            ),
        )
        .where(
            Message.id == message_id,
            Message.account_id.in_(principal.access_scope.account_ids),
            Message.project_id.in_(principal.access_scope.project_ids),
            Message.chat_id == conversation["chat_id"],
            Message.business_connection_id.is_not_distinct_from(
                conversation["business_connection_id"]
            ),
            (
                (Message.source == "telegram")
                | (
                    (Message.source == "gate-approved-document")
                    & (Message.direction == MessageDirection.OUTBOUND.value)
                    & (Message.event_type == "telegram_business_document")
                )
            ),
        )
    )
    row = result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="attachment_message_not_found",
            message="Selected Telegram message was not found in the allowed scope",
        )
    message, telegram_update, gate_event, telegram_receipt = row
    receipt_payload = (
        validated_telegram_receipt_payload(
            telegram_receipt.payload,
            receipt_update_id=telegram_receipt.update_id,
            message_update_id=message.telegram_update_id,
            event_type=message.event_type,
            telegram_message_id=message.telegram_message_id,
            chat_id=message.chat_id,
            business_connection_id=message.business_connection_id,
        )
        if telegram_receipt is not None
        else None
    )
    if message.source == "gate-approved-document":
        sent_payload = validated_telegram_sent_document_payload(
            message.payload,
            telegram_message_id=message.telegram_message_id,
            chat_id=message.chat_id,
            business_connection_id=message.business_connection_id,
        )
        attachments = extract_telegram_attachments(sent_payload)
    else:
        attachments = extract_telegram_attachments(
            telegram_update.payload if telegram_update is not None else None,
            gate_event.payload if gate_event is not None else None,
            message.payload,
            receipt_payload,
        )
    if attachment_index >= len(attachments):
        return _tool_error_result(
            request_id=request_id,
            code="attachment_not_found",
            message="Selected message has no attachment at that index",
        )
    attachment = attachments[attachment_index]
    if not attachment.download_available:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_attachment_cloud_limit_exceeded",
            message=(
                "Telegram's hosted Bot API cannot download this attachment "
                "because its declared size exceeds 20 MB"
            ),
        )
    if not settings.telegram_bot_token:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_attachment_download_unavailable",
            message="Telegram attachment download is not configured",
        )

    try:
        data, media_type = await _download_telegram_attachment(
            attachment=attachment,
            bot_token=settings.telegram_bot_token,
        )
    except TelegramAttachmentError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message="Telegram attachment download failed closed",
        )
    return _attachment_content_tool_result(
        request_id=request_id,
        context=context,
        message_id=message_id,
        attachment_index=attachment_index,
        attachment=attachment,
        data=data,
        media_type=media_type,
    )


async def _download_telegram_attachment(
    *,
    attachment: TelegramAttachmentReference,
    bot_token: str,
) -> tuple[bytes, str]:
    api = "https://api.telegram.org"
    timeout = httpx.Timeout(60.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            metadata_response = await http_client.post(
                f"{api}/bot{bot_token}/getFile",
                json={"file_id": attachment.file_id},
            )
            metadata = metadata_response.json()
            if (
                metadata_response.is_error
                or not isinstance(metadata, dict)
                or metadata.get("ok") is not True
                or not isinstance(metadata.get("result"), dict)
            ):
                raise TelegramAttachmentError(
                    "telegram_attachment_get_file_rejected"
                )
            file_metadata = metadata["result"]
            file_size = file_metadata.get("file_size")
            if (
                type(file_size) is int
                and file_size > TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES
            ):
                raise TelegramAttachmentError(
                    "telegram_attachment_cloud_limit_exceeded"
                )
            file_path = file_metadata.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                raise TelegramAttachmentError(
                    "telegram_attachment_file_path_missing"
                )
            path = PurePosixPath(file_path.strip().lstrip("/"))
            if any(part in {".", ".."} for part in path.parts):
                raise TelegramAttachmentError(
                    "telegram_attachment_file_path_invalid"
                )
            safe_path = quote(path.as_posix(), safe="/")
            data = bytearray()
            media_type = attachment.mime_type
            async with http_client.stream(
                "GET",
                f"{api}/file/bot{bot_token}/{safe_path}",
            ) as response:
                response.raise_for_status()
                header_type = response.headers.get("content-type")
                if isinstance(header_type, str) and header_type.strip():
                    media_type = header_type.split(";", maxsplit=1)[0].strip()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if (
                        len(data)
                        > TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES
                    ):
                        raise TelegramAttachmentError(
                            "telegram_attachment_cloud_limit_exceeded"
                        )
    except TelegramAttachmentError:
        raise
    except (httpx.HTTPError, ValueError):
        raise TelegramAttachmentError(
            "telegram_attachment_download_failed"
        ) from None
    if not data:
        raise TelegramAttachmentError("telegram_attachment_empty")
    return bytes(data), media_type or "application/octet-stream"


def _attachment_content_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    message_id: UUID,
    attachment_index: int,
    attachment: TelegramAttachmentReference,
    data: bytes,
    media_type: str,
) -> JsonObject:
    encoded = base64.b64encode(data).decode("ascii")
    if media_type.startswith("image/"):
        content_block: JsonObject = {
            "type": "image",
            "data": encoded,
            "mimeType": media_type,
        }
    elif media_type.startswith("audio/"):
        content_block = {
            "type": "audio",
            "data": encoded,
            "mimeType": media_type,
        }
    else:
        filename = quote(attachment.filename, safe="")
        content_block = {
            "type": "resource",
            "resource": {
                "uri": (
                    f"gate://telegram-attachment/{message_id}/"
                    f"{attachment_index}/{filename}"
                ),
                "mimeType": media_type,
                "blob": encoded,
            },
        }
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Owner-selected Telegram attachment: "
                        f"{attachment.filename} ({media_type}, {len(data)} bytes)."
                    ),
                },
                content_block,
            ],
            "structuredContent": context,
            "isError": False,
        },
    )
