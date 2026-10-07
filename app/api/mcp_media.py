"""Scoped Work image upload and byte-backed MCP preview."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.reply_drafts import ReplyDraftError
from app.application.reply_media import (
    MAX_MEDIA_BASE64_LENGTH,
    PrepareReplyMediaCommand,
    PrepareReplyMediaService,
    ReplyMediaInput,
)
from app.core.mcp_security import Principal
from app.infrastructure.reply_media import SqlReplyMediaWriter, read_reply_media_preview
from app.models.domain import ReplyDraft
from app.models.reply_media import ReplyMediaPackage

JsonObject = dict[str, Any]
SHA_SCHEMA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
KEY_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"}
ITEM_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "filename": {"type": "string", "minLength": 1, "maxLength": 255},
        "media_type": {"type": "string", "enum": ["image/png", "image/jpeg"]},
        "content_base64": {"type": "string", "minLength": 4, "maxLength": MAX_MEDIA_BASE64_LENGTH},
        "expected_sha256": SHA_SCHEMA,
        "caption": {"type": "string", "maxLength": 4096},
        "caption_format": {"type": "string", "enum": ["plain", "markdown"]},
    },
    "required": ["filename", "media_type", "content_base64", "expected_sha256"],
    "additionalProperties": False,
}
PREPARE_INPUT_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "format": "uuid"},
        "mode": {
            "type": "string",
            "enum": ["photo", "album", "rich_photo", "rich_slideshow", "rich_collage"],
        },
        "items": {"type": "array", "minItems": 1, "maxItems": 10, "items": ITEM_SCHEMA},
        "staging_key": KEY_SCHEMA,
        "execution_id": KEY_SCHEMA,
        "execution_expires_at": {"type": "string", "format": "date-time"},
        "follow_up": {"type": "boolean"},
        "rich_content": {"type": "object"},
    },
    "required": ["task_id", "mode", "items", "staging_key", "execution_id", "execution_expires_at"],
    "additionalProperties": False,
}
PREVIEW_INPUT_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {"package_id": {"type": "string", "format": "uuid"}},
    "required": ["package_id"],
    "additionalProperties": False,
}
MEDIA_OUTPUT_SCHEMA: JsonObject = {
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "format": "uuid"},
                "draft_id": {"type": "string", "format": "uuid"},
                "task_id": {"type": "string", "format": "uuid"},
                "snapshot": {"type": "object"},
                "package_sha256": SHA_SCHEMA,
                "expected_message_count": {"type": "integer", "minimum": 1, "maximum": 10},
                "expires_at": {"type": "string", "format": "date-time"},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": {"type": "object"},
                },
                "confirmation": {"type": "object"},
                "sent": {"type": "boolean", "const": False},
                "approval_required": {"type": "boolean", "const": True},
                "idempotent_replay": {"type": "boolean"},
            },
            "required": [
                "package_id",
                "draft_id",
                "task_id",
                "snapshot",
                "package_sha256",
                "expected_message_count",
                "expires_at",
                "items",
                "confirmation",
                "sent",
                "approval_required",
                "idempotent_replay",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"error": {"type": "string"}},
            "required": ["error"],
            "additionalProperties": False,
        },
    ],
}


def _result(
    request_id: Any, content: JsonObject, blocks: list[JsonObject], *, error: bool = False
) -> JsonObject:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": blocks,
            "structuredContent": content,
            "isError": error,
        },
    }


async def media_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    prepare: bool,
) -> JsonObject:
    try:
        schema = PREPARE_INPUT_SCHEMA if prepare else PREVIEW_INPUT_SCHEMA
        if arguments.keys() - schema["properties"].keys():
            raise ValueError("Unknown arguments are not supported")
        if prepare:
            for key in ("task_id", "mode", "staging_key", "execution_id", "execution_expires_at"):
                if not isinstance(arguments.get(key), str):
                    raise ValueError(f"{key} must be a string")
            raw_items = arguments.get("items")
            if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 10:
                raise ValueError("items must contain 1–10 images")
            items = []
            for raw in raw_items:
                if not isinstance(raw, dict) or raw.keys() - ITEM_SCHEMA["properties"].keys():
                    raise ValueError("Invalid image fields")
                if any(not isinstance(raw.get(key), str) for key in ITEM_SCHEMA["required"]):
                    raise ValueError("Exact image metadata and base64 bytes are required")
                if not isinstance(raw.get("caption", ""), str) or raw.get(
                    "caption_format", "plain"
                ) not in {"plain", "markdown"}:
                    raise ValueError("Invalid image caption")
                items.append(ReplyMediaInput(**raw))
            follow_up = arguments.get("follow_up", False)
            rich = arguments.get("rich_content")
            if type(follow_up) is not bool or (
                "rich_content" in arguments and not isinstance(rich, dict)
            ):
                raise ValueError("Invalid follow_up or rich_content")
            deadline = datetime.fromisoformat(
                arguments["execution_expires_at"].replace("Z", "+00:00")
            )
            if deadline.tzinfo is None:
                raise ValueError("execution_expires_at must include a timezone")
            prepared = await PrepareReplyMediaService(SqlReplyMediaWriter(session)).execute(
                PrepareReplyMediaCommand(
                    principal=principal,
                    task_id=UUID(arguments["task_id"]),
                    mode=arguments["mode"],
                    items=tuple(items),
                    staging_key=arguments["staging_key"],
                    execution_id=arguments["execution_id"],
                    execution_expires_at=deadline,
                    follow_up=follow_up,
                    rich_content=rich,
                )
            )
            package_id, replay = prepared.package_id, prepared.idempotent_replay
            await session.commit()
        else:
            if not isinstance(arguments.get("package_id"), str):
                raise ValueError("package_id must be a UUID")
            package_id, replay = UUID(arguments["package_id"]), False
        images = await read_reply_media_preview(session, principal, package_id)
        # The scoped reader has checked authorization, snapshot integrity and every byte hash.
        package = await session.get(ReplyMediaPackage, package_id)
        if package is None:
            raise ReplyDraftError(code="media_not_found", message="Media package unavailable")
        draft = await session.get(ReplyDraft, package.reply_draft_id)
        if draft is None:
            raise ReplyDraftError(code="media_not_found", message="Media draft unavailable")
        content: JsonObject = {
            "package_id": str(package.id),
            "draft_id": str(package.reply_draft_id),
            "task_id": str(draft.reply_task_id),
            "snapshot": package.snapshot_json,
            "package_sha256": package.package_sha256,
            "expected_message_count": package.expected_message_count,
            "expires_at": package.expires_at.isoformat(),
            "items": [item.to_dict() for item, _ in images],
            "confirmation": {
                "expected_media_package_sha256": package.package_sha256,
                "expected_account": package.snapshot_json["account_label"],
                "expected_recipient_chat_id": package.snapshot_json["scope"]["recipient_chat_id"],
            },
            "sent": False,
            "approval_required": True,
            "idempotent_replay": replay,
        }
        blocks: list[JsonObject] = [
            {
                "type": "text",
                "text": (
                    "Prepared images below are in item order. Show images, complete snapshot, "
                    "captions, "
                    "transforms, sender, recipient and package hash before exact approval. "
                    "Not sent."
                ),
            }
        ]
        blocks.extend(
            {
                "type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mimeType": item.media_type,
            }
            for item, data in images
        )
        return _result(request_id, content, blocks)
    except ReplyDraftError as exc:
        return _result(
            request_id, {"error": exc.code}, [{"type": "text", "text": exc.message}], error=True
        )
    except (ValueError, TypeError, OverflowError):
        return _result(
            request_id,
            {"error": "invalid_arguments"},
            [
                {
                    "type": "text",
                    "text": "Invalid media arguments; exact structured upload is required",
                }
            ],
            error=True,
        )
