"""Owner-scoped MCP readback for Watch and Calls archives."""

import base64
import hashlib
import json
import mimetypes
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audio_processing_history import (
    call_processing_history,
    watch_processing_history,
)
from app.api.mcp_results import (
    jsonrpc_result as _jsonrpc_result,
)
from app.api.mcp_results import (
    tool_error_result as _tool_error_result,
)
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.infrastructure.watch_voice_post_payment import protected_post_payment_receipt
from app.infrastructure.watch_voice_recovery import protected_recovery_receipt
from app.models.domain import (
    UNCLASSIFIED_PROJECT_ID,
    PhoneCallRecording,
)
from app.models.watch_voice import (
    WatchEarlyCard,
    WatchVoiceNote,
)

JsonObject = dict[str, Any]


async def _watch_voice_tool_result(
    *,
    request_id: Any,
    selector: str,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    if (
        principal.access_scope.workspace_id != settings.gate_workspace_id
        or UNCLASSIFIED_PROJECT_ID not in principal.access_scope.project_ids
    ):
        return _tool_error_result(
            request_id=request_id,
            code="watch_voice_scope_denied",
            message="Watch voice is outside the allowed owner scope",
        )
    prefix, ref = selector.split(":", 1)
    try:
        note_id = None if ref == "latest" else str(UUID(ref))
    except ValueError:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="Exact watch voice UUID or latest is required",
        )
    query = select(WatchVoiceNote).where(
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
    )
    if note_id is not None:
        query = query.where(WatchVoiceNote.id == note_id)
    row = await session.scalar(query.order_by(WatchVoiceNote.received_at.desc()).limit(1))
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="watch_voice_archive_empty",
            message="No watch voice archive exists in this owner scope",
        )
    context = {
        "task_id": row.id,
        "task_ref": row.id[:8],
        "status": "watch_voice_" + row.transcription_state,
        "reply_required": False,
        "account": {"id": row.id, "alias": "watch_voice", "label": "WATCH VOICE"},
        "conversation": {"id": row.id, "chat_id": "watch-voice", "business_connection_id": None},
        "counterparty": {
            "platform": "watch_voice_dropbox",
            "platform_user_id": None,
            "display_name": None,
            "username": None,
            "label": None,
        },
        "messages": [],
        "count": 0,
        "limit": 1,
        "next_cursor": None,
        "has_more": False,
        "processing_history": watch_processing_history(row),
    }
    if prefix == "watch-voice-file":
        if hashlib.sha256(row.data).hexdigest() != row.sha256 or len(row.data) != row.size_bytes:
            return _tool_error_result(
                request_id=request_id,
                code="watch_voice_integrity_failed",
                message="Watch original integrity failed",
            )
        return _voice_file_tool_result(
            request_id=request_id,
            context=context,
            archive_id=row.id,
            data=row.data,
            media_type=row.media_type,
            filename_prefix="watch-voice",
        )
    issue_url = row.issue_url
    if issue_url is None:
        issue_url = await session.scalar(
            select(WatchEarlyCard.issue_url).where(
                WatchEarlyCard.id == row.id,
                WatchEarlyCard.workspace_key == row.workspace_key,
                WatchEarlyCard.account_id == row.account_id,
                WatchEarlyCard.state == "saved",
            )
        )
    receipt = {
        "event_id": row.id,
        "source_file_id": row.file_id,
        "revision": row.revision,
        "audio_sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "recorded_at": None,
        "dropbox_modified_at": row.dropbox_modified_at.isoformat(),
        "gate_received_at": row.received_at.isoformat(),
        "transcribed_at": row.transcribed_at.isoformat() if row.transcribed_at else None,
        "transcript": row.transcript,
        "transcription_state": row.transcription_state,
        "delivery_state": row.delivery_state,
        "issue_url": issue_url,
        "github_received_at": row.github_received_at.isoformat()
        if row.github_received_at
        else None,
        "last_error": row.last_error,
        "model": row.model,
        "processing_history": context["processing_history"],
    }
    if settings.gate_watch_mark_updates_enabled and row.mark_publish_version:
        publication = (await session.execute(
            select(
                WatchEarlyCard.mark_published_version, WatchEarlyCard.mark_published_at,
                WatchEarlyCard.mark_update_blocked, WatchEarlyCard.mark_update_error,
            ).where(
                WatchEarlyCard.id == row.id,
                WatchEarlyCard.workspace_key == row.workspace_key,
                WatchEarlyCard.account_id == row.account_id,
                WatchEarlyCard.file_id == row.file_id,
                WatchEarlyCard.revision == row.revision,
            )
        )).one_or_none()
        if publication is not None:
            verified_at = publication.mark_published_at
            receipt["mark_publication"] = {
                "requested_version": row.mark_publish_version,
                "verified_version": publication.mark_published_version,
                "verified_at": verified_at.isoformat() if verified_at else None,
                "blocked": publication.mark_update_blocked,
                "error": publication.mark_update_error,
            }
    if row.model == "gpt-4o-transcribe":
        receipt["recovery"] = await protected_recovery_receipt(session, row)
        receipt["post_payment"] = await protected_post_payment_receipt(session, row)
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [{"type": "text", "text": json.dumps(receipt, ensure_ascii=False)}],
            "structuredContent": context,
            "isError": False,
        },
    )


def _parse_latest_call_selector(raw_task_id: str) -> str | None:
    selectors = {
        "call:latest": "audio",
        "call:latest-file": "file",
        "call:latest-transcript": "transcript",
    }
    if raw_task_id in selectors:
        return selectors[raw_task_id]
    if raw_task_id.startswith("call:latest"):
        raise ValueError("invalid latest call selector")
    return None


async def _latest_phone_call_tool_result(
    *,
    request_id: Any,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
    payload_kind: str,
) -> JsonObject:
    recording = await session.scalar(
        select(PhoneCallRecording)
        .where(
            PhoneCallRecording.workspace_key == settings.gate_workspace_id,
            PhoneCallRecording.project_id.in_(principal.access_scope.project_ids),
        )
        .order_by(
            PhoneCallRecording.received_at.desc(),
            PhoneCallRecording.id.desc(),
        )
        .limit(1)
    )
    if recording is None:
        return _tool_error_result(
            request_id=request_id,
            code="phone_call_archive_empty",
            message="No archived mobile call is available in the allowed scope",
        )
    recording_id = str(recording.id)
    context: JsonObject = {
        "task_id": recording_id,
        "task_ref": recording_id[:8],
        "status": f"phone_call_{recording.transcription_status}",
        "reply_required": False,
        "account": {
            "id": recording_id,
            "alias": "phone_calls",
            "label": "PHONE CALLS",
        },
        "conversation": {
            "id": recording_id,
            "chat_id": "phone-call",
            "business_connection_id": None,
        },
        "counterparty": {
            "platform": "phone_call_dropbox",
            "platform_user_id": None,
            "display_name": None,
            "username": None,
            "label": None,
        },
        "messages": [],
        "count": 0,
        "limit": 1,
        "next_cursor": None,
        "has_more": False,
        "processing_history": call_processing_history(recording),
    }
    effective_media_type = _effective_phone_call_media_type(
        recording.media_type,
        recording.filename,
    )
    if payload_kind == "transcript":
        if (
            recording.transcription_status != "transcribed"
            or recording.transcript_text is None
        ):
            return _tool_error_result(
                request_id=request_id,
                code=(
                    recording.transcription_reason_code
                    or "phone_call_transcription_pending"
                ),
                message="The latest mobile call has no completed transcript",
                processing_history=context["processing_history"],
            )
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "content": [{"type": "text", "text": recording.transcript_text}],
                "structuredContent": context,
                "isError": False,
            },
        )
    if payload_kind == "file":
        return _voice_file_tool_result(
            request_id=request_id,
            context=context,
            archive_id=recording_id,
            data=recording.data,
            media_type=effective_media_type,
            filename_prefix="phone-call",
        )
    return _voice_audio_tool_result(
        request_id=request_id,
        context=context,
        data=recording.data,
        media_type=effective_media_type,
    )


def _effective_phone_call_media_type(media_type: str, filename: str) -> str:
    if media_type.startswith("audio/"):
        return media_type
    guessed = mimetypes.guess_type(filename)[0]
    if guessed is not None and guessed.startswith("audio/"):
        return guessed
    if filename.casefold().endswith(".m4a"):
        return "audio/mp4"
    return media_type


def _voice_audio_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    data: bytes,
    media_type: str,
) -> JsonObject:
    if not data or not media_type.startswith("audio/"):
        raise ValueError("invalid audio content")
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": "Owner-selected Telegram voice attached.",
                },
                {
                    "type": "audio",
                    "data": base64.b64encode(data).decode("ascii"),
                    "mimeType": media_type,
                },
            ],
            "structuredContent": context,
            "isError": False,
        },
    )


def _voice_file_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    archive_id: str,
    data: bytes,
    media_type: str,
    filename_prefix: str = "telegram-voice",
) -> JsonObject:
    if not data or not media_type.startswith("audio/"):
        raise ValueError("invalid audio content")
    extension = "ogg" if media_type in {"audio/ogg", "audio/opus"} else "audio"
    file_payload = {
        "version": 1,
        "filename": f"{filename_prefix}-{archive_id}.{extension}",
        "mime_type": media_type,
        "data_base64": base64.b64encode(data).decode("ascii"),
    }
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        file_payload,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "structuredContent": context,
            "isError": False,
        },
    )
