"""Resolve MCP reply context and dispatch its exact read selectors."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mcp_attachments import _attachment_reply_context_tool_result
from app.api.mcp_audio_archive import (
    _latest_phone_call_tool_result,
    _parse_latest_call_selector,
    _watch_voice_tool_result,
)
from app.api.mcp_results import JsonObject
from app.api.mcp_results import bounded_limit as _bounded_limit
from app.api.mcp_results import tool_error_result as _tool_error_result
from app.api.mcp_results import tool_result as _tool_result
from app.api.mcp_telegram_voice import (
    _latest_voice_archive_tool_result,
    _parse_latest_voice_file_selector,
    _parse_latest_voice_selector,
    _parse_latest_voice_transcript_selector,
    _voice_reply_context_tool_result,
)
from app.application.mcp_inbox import get_reply_context
from app.application.telegram_replies import format_telegram_reply
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.models.domain import MessageDirection


async def _get_reply_context_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    raw_task_id = arguments.get("task_id")
    if not isinstance(raw_task_id, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="task_id is required",
        )
    if raw_task_id.startswith("watch-voice:") or raw_task_id.startswith("watch-voice-file:"):
        return await _watch_voice_tool_result(
            request_id=request_id, selector=raw_task_id, principal=principal,
            session=session, settings=settings,
        )
    try:
        call_selector = _parse_latest_call_selector(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest call selector is invalid",
        )
    if call_selector is not None:
        return await _latest_phone_call_tool_result(
            request_id=request_id,
            principal=principal,
            session=session,
            settings=settings,
            payload_kind=call_selector,
        )
    try:
        latest_voice_transcript_alias = _parse_latest_voice_transcript_selector(
            raw_task_id
        )
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest voice transcript selector is invalid",
        )
    if latest_voice_transcript_alias is not None:
        return await _latest_voice_archive_tool_result(
            request_id=request_id,
            account_alias=latest_voice_transcript_alias,
            principal=principal,
            session=session,
            settings=settings,
            transcript_payload=True,
        )
    try:
        latest_voice_file_alias = _parse_latest_voice_file_selector(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest voice file selector is invalid",
        )
    if latest_voice_file_alias is not None:
        return await _latest_voice_archive_tool_result(
            request_id=request_id,
            account_alias=latest_voice_file_alias,
            principal=principal,
            session=session,
            file_payload=True,
        )
    try:
        latest_voice_alias = _parse_latest_voice_selector(raw_task_id)
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="latest voice selector is invalid",
        )
    if latest_voice_alias is not None:
        return await _latest_voice_archive_tool_result(
            request_id=request_id,
            account_alias=latest_voice_alias,
            principal=principal,
            session=session,
        )
    try:
        task_id, voice_message_id, attachment_selection = (
            _parse_reply_context_selector(raw_task_id)
        )
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "task_id must be a UUID or an exact task:voice:message "
                "or task:attachment:message:index selector"
            ),
        )

    raw_events_cursor = arguments.get("dialog_events_cursor")
    if raw_events_cursor is not None and not isinstance(raw_events_cursor, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="dialog_events_cursor must be a string",
        )
    raw_cursor = arguments.get("cursor")
    if raw_cursor is not None and not isinstance(raw_cursor, str):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="cursor must be an opaque string",
        )
    try:
        context = await get_reply_context(
            session=session,
            scope=principal.access_scope,
            task_id=task_id,
            limit=_bounded_limit(arguments.get("limit"), default=20, maximum=100),
            cursor=raw_cursor,
            **(
                {"dialog_events_cursor": raw_events_cursor} if raw_events_cursor is not None else {}
            ),
        )
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="reply context cursor is invalid for this task",
        )
    if context is None:
        return _tool_error_result(
            request_id=request_id,
            code="task_not_found",
            message="Reply task not found in the allowed scope",
        )
    if voice_message_id is not None:
        return await _voice_reply_context_tool_result(
            request_id=request_id,
            context=context,
            message_id=voice_message_id,
            principal=principal,
            session=session,
            settings=settings,
        )
    if attachment_selection is not None:
        attachment_message_id, attachment_index = attachment_selection
        return await _attachment_reply_context_tool_result(
            request_id=request_id,
            context=context,
            message_id=attachment_message_id,
            attachment_index=attachment_index,
            principal=principal,
            session=session,
            settings=settings,
        )
    return _tool_result(
        context,
        _format_reply_context_text(context),
        request_id,
    )


def _parse_reply_context_selector(
    raw_task_id: str,
) -> tuple[UUID, UUID | None, tuple[UUID, int] | None]:
    attachment_parts = raw_task_id.split(":attachment:")
    if len(attachment_parts) == 2 and all(attachment_parts):
        task_raw, selection_raw = attachment_parts
        message_parts = selection_raw.rsplit(":", maxsplit=1)
        if (
            len(message_parts) != 2
            or not all(message_parts)
            or not message_parts[1].isdigit()
        ):
            raise ValueError("invalid attachment selector")
        attachment_index = int(message_parts[1])
        if attachment_index < 0 or attachment_index > 32:
            raise ValueError("invalid attachment selector")
        return (
            UUID(task_raw),
            None,
            (UUID(message_parts[0]), attachment_index),
        )
    if len(attachment_parts) != 1:
        raise ValueError("invalid attachment selector")

    voice_parts = raw_task_id.split(":voice:")
    if len(voice_parts) == 1:
        return UUID(raw_task_id), None, None
    if len(voice_parts) != 2 or not all(voice_parts):
        raise ValueError("invalid voice selector")
    return UUID(voice_parts[0]), UUID(voice_parts[1]), None


def _format_reply_context_text(context: JsonObject) -> str:
    account = context["account"]
    counterparty = context["counterparty"]
    lines = [
        f"Gate reply context — [{account['label']}] {counterparty['label']}",
        f"task: {context['task_ref']}",
        f"chat: {context['conversation']['chat_id']}",
        "",
        f"Context ready: {context.get('context_ready', False)}; "
        f"reason: {context.get('context_reason') or 'none'}",
        "Snapshot is a precondition, never approval. Read structured dialog_events too.",
        f"Recent messages: {context['count']}",
    ]
    for sequence in context.get("reply_sequences", []):
        lines.append(
            f"Series {sequence['sequence_id']}: {sequence['state']}; "
            f"reason: {sequence['stop_reason'] or 'none'}; "
            f"sent {sequence['sent_count']}, remaining {sequence['remaining_count']}, "
            f"ambiguous {sequence['ambiguous_count']}. "
            "Auto-resume: false."
        )
        if sequence.get("stop_message"):
            lines.append(sequence["stop_message"])
    for message in context["messages"]:
        direction = "IN" if message["direction"] == MessageDirection.INBOUND.value else "OUT"
        preview = message["text_preview"] or {
            "no_text": "<no text>",
            "source_unavailable": "<text source unavailable>",
            "processing_failed": "<text processing failed>",
        }.get(message.get("text_status"), "<empty>")
        lines.extend(
            [
                "",
                f"[{direction}] {preview}",
            ]
        )
        if message.get("reply_to") is not None:
            lines.extend(format_telegram_reply(message["reply_to"]))
        for attachment in message.get("attachments", []):
            availability = (
                "available on request"
                if attachment["download_available"]
                else attachment["download_state"]
            )
            lines.append(
                "attachment "
                f"{attachment['index']}: {attachment['filename']} "
                f"({attachment['mime_type']}, "
                f"{attachment['size_bytes'] or 'size unknown'}, "
                f"{availability}); selector={attachment['selector']}"
            )
    return "\n".join(lines)
