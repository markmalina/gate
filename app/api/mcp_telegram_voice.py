"""MCP operations for scoped Telegram voice readback."""

import hashlib
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import (
    or_,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.api.audio_processing_history import telegram_processing_history
from app.api.mcp_audio_archive import (
    _voice_audio_tool_result,
    _voice_file_tool_result,
)
from app.api.mcp_results import JsonObject
from app.api.mcp_results import jsonrpc_result as _jsonrpc_result
from app.api.mcp_results import tool_error_result as _tool_error_result
from app.application.telegram_voice_archive import (
    PersistTelegramVoiceTranscriptCommand,
    TelegramVoiceRecognition,
    TelegramVoiceTranscriptStatus,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    TelegramVoiceError,
    TelegramVoiceOutcome,
    TelegramVoiceReference,
    TelegramVoiceTranscriptionService,
    TranscribeTelegramVoiceCommand,
)
from app.application.telegram_voice_update import extract_telegram_voice
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.telegram_voice import (
    TelegramVoiceDownloadConfig,
    TelegramVoiceDownloader,
    TelegramVoiceNormalizer,
)
from app.infrastructure.telegram_voice_archive import SessionFactoryTelegramVoiceArchiveStore
from app.models.domain import (
    Account,
    GateEvent,
    Message,
    MessageDirection,
    TelegramUpdate,
    TelegramVoiceArchive,
)


def _parse_latest_voice_selector(raw_task_id: str) -> str | None:
    prefix = "voice:latest:"
    if not raw_task_id.startswith(prefix):
        return None
    alias = raw_task_id.removeprefix(prefix)
    if (
        not alias
        or len(alias) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in alias)
    ):
        raise ValueError("invalid latest voice selector")
    return alias


def _parse_latest_voice_file_selector(raw_task_id: str) -> str | None:
    prefix = "voice:latest-file:"
    if not raw_task_id.startswith(prefix):
        return None
    alias = raw_task_id.removeprefix(prefix)
    if (
        not alias
        or len(alias) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in alias)
    ):
        raise ValueError("invalid latest voice file selector")
    return alias


def _parse_latest_voice_transcript_selector(raw_task_id: str) -> str | None:
    prefix = "voice:latest-transcript:"
    if not raw_task_id.startswith(prefix):
        return None
    alias = raw_task_id.removeprefix(prefix)
    if (
        not alias
        or len(alias) > 64
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in alias)
    ):
        raise ValueError("invalid latest voice transcript selector")
    return alias


def _voice_account_selector_scope(
    account_selector: str,
) -> tuple[str, str | None]:
    country_code = account_selector if account_selector in {"ru", "by"} else None
    return account_selector, country_code


def _voice_account_selector_filter(account_selector: str) -> Any:
    exact_alias, country_code = _voice_account_selector_scope(account_selector)
    if country_code is None:
        return Account.alias == exact_alias
    return or_(
        Account.alias == exact_alias,
        Account.country_code == country_code,
        Account.alias.endswith(f"_{country_code}"),
    )


async def _latest_voice_archive_tool_result(
    *,
    request_id: Any,
    account_alias: str,
    principal: Principal,
    session: AsyncSession,
    file_payload: bool = False,
    settings: Settings | None = None,
    transcript_payload: bool = False,
) -> JsonObject:
    result = await session.execute(
        select(TelegramVoiceArchive, Account)
        .join(Account, Account.id == TelegramVoiceArchive.account_id)
        .where(
            _voice_account_selector_filter(account_alias),
            Account.id.in_(principal.access_scope.account_ids),
            TelegramVoiceArchive.account_id.in_(
                principal.access_scope.account_ids
            ),
            TelegramVoiceArchive.project_id.in_(
                principal.access_scope.project_ids
            ),
        )
        .order_by(
            TelegramVoiceArchive.received_at.desc(),
            TelegramVoiceArchive.id.desc(),
        )
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_archive_empty",
            message="No archived Telegram voice is available for this account",
        )
    archive, account = row
    archive_id = str(archive.id)
    context = {
        "task_id": archive_id,
        "task_ref": archive_id[:8],
        "status": "archived_voice",
        "reply_required": False,
        "account": {
            "id": str(account.id),
            "alias": account.alias,
            "label": account.alias.upper(),
        },
        "conversation": {
            "id": archive_id,
            "chat_id": "archived-voice",
            "business_connection_id": None,
        },
        "counterparty": {
            "platform": "telegram",
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
        "processing_history": telegram_processing_history(archive),
    }
    if transcript_payload:
        if settings is None:
            raise ValueError("settings are required for transcription")
        return await _voice_transcript_tool_result(
            request_id=request_id,
            context=context,
            archive=archive,
            session=session,
            settings=settings,
        )
    if file_payload:
        return _voice_file_tool_result(
            request_id=request_id,
            context=context,
            archive_id=archive_id,
            data=archive.data,
            media_type=archive.media_type,
        )
    return _voice_audio_tool_result(
        request_id=request_id,
        context=context,
        data=archive.data,
        media_type=archive.media_type,
    )


class _ArchivedVoiceDownloader:
    def __init__(self, voice: DownloadedVoice) -> None:
        self._voice = voice

    async def download(
        self,
        _reference: TelegramVoiceReference,
    ) -> DownloadedVoice:
        return self._voice


async def _voice_transcript_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    archive: TelegramVoiceArchive,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    if (
        archive.transcription_status == "transcribed"
        and archive.transcript_text is not None
    ):
        return _jsonrpc_result(
            request_id=request_id,
            result={
                "content": [
                    {
                        "type": "text",
                        "text": archive.transcript_text,
                    },
                ],
                "structuredContent": context,
                "isError": False,
            },
        )
    if archive.transcription_status is not None:
        return _tool_error_result(
            request_id=request_id,
            code=(
                archive.transcription_reason_code
                or "telegram_voice_transcription_blocked"
            ),
            message="Stored OpenAI voice transcription has no text",
            processing_history=context["processing_history"],
        )
    if archive.recognition_started_at is not None:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_recognition_already_claimed",
            message="This archived voice already has a recognition claim",
            processing_history=context["processing_history"],
        )

    api_key = settings.openai_api_key.get_secret_value()
    if not settings.gate_telegram_voice_transcription_enabled or not api_key:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_transcription_unavailable",
            message="OpenAI voice transcription is not ready",
            processing_history=context["processing_history"],
        )
    if session.bind is None:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_transcription_unavailable",
            message="Voice transcription database binding is unavailable",
            processing_history=context["processing_history"],
        )
    if (
        len(archive.data) != archive.size_bytes
        or hashlib.sha256(archive.data).hexdigest() != archive.sha256
    ):
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_archive_integrity_failed",
            message="Stored voice original integrity failed",
            processing_history=context["processing_history"],
        )

    occurred_at = datetime.now(UTC)
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    store = SessionFactoryTelegramVoiceArchiveStore(sessions)
    recognition = TelegramVoiceRecognition(str(archive.id), store)
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        service = TelegramVoiceTranscriptionService(
            downloader=_ArchivedVoiceDownloader(
                DownloadedVoice(
                    data=archive.data,
                    filename=archive.filename,
                    media_type=archive.media_type,
                )
            ),
            normalizer=TelegramVoiceNormalizer(),
            budget=SqlBillingBudgetService(sessions),
            speech_to_text=OpenAITranscriptionClient(
                config=OpenAITranscriptionConfig(
                    api_key=api_key,
                    enabled=True,
                    model=settings.gate_telegram_voice_transcription_model,
                    base_url=settings.openai_api_base_url,
                ),
                http=http_client,
            ),
        )
        try:
            transcription = await service.transcribe(
                TranscribeTelegramVoiceCommand(
                    voice=TelegramVoiceReference(
                        file_id=f"archive-{archive.id}",
                        file_unique_id=str(archive.id),
                        declared_size_bytes=archive.size_bytes,
                        duration_seconds=archive.duration_seconds,
                    ),
                    workspace_key=settings.gate_workspace_id,
                    connector_ref="telegram_business",
                    reservation_key=f"mcp-transcription-v2:{archive.id}",
                    period_key=occurred_at.strftime("%Y-%m"),
                    requested_microusd=max(
                        100,
                        archive.duration_seconds
                        * settings.gate_telegram_voice_reserve_microusd_per_second,
                    ),
                    occurred_at=occurred_at,
                    language="ru",
                    enabled=True,
                    on_recognition_start=recognition.start,
                )
            )
        except Exception:
            await recognition.interrupt(datetime.now(UTC), "telegram_voice_transcription_failed")
            await session.refresh(archive)
            return _tool_error_result(
                request_id=request_id,
                code="telegram_voice_transcription_failed",
                message="OpenAI voice transcription failed closed",
                processing_history=telegram_processing_history(archive),
            )

    try:
        await store.persist_transcript(
            PersistTelegramVoiceTranscriptCommand(
                archive_id=str(archive.id),
                status=TelegramVoiceTranscriptStatus(transcription.outcome.value),
                transcribed_at=datetime.now(UTC),
                text=transcription.alpha_input_text,
                model=settings.gate_telegram_voice_transcription_model,
                reason_code=transcription.reason_code,
                recognition_started_at=recognition.started_at,
            )
        )
    except Exception:
        await recognition.interrupt(datetime.now(UTC), "telegram_voice_transcript_persist_failed")
        await session.refresh(archive)
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_transcript_persist_failed",
            message="Voice transcription result could not be stored",
            processing_history=telegram_processing_history(archive),
        )
    await session.refresh(archive)
    context["processing_history"] = telegram_processing_history(archive)

    if (
        transcription.outcome is not TelegramVoiceOutcome.TRANSCRIBED
        or transcription.alpha_input_text is None
    ):
        return _tool_error_result(
            request_id=request_id,
            code=transcription.reason_code,
            message="OpenAI voice transcription did not return text",
            processing_history=context["processing_history"],
        )
    return _jsonrpc_result(
        request_id=request_id,
        result={
            "content": [
                {
                    "type": "text",
                    "text": transcription.alpha_input_text,
                },
            ],
            "structuredContent": context,
            "isError": False,
        },
    )


async def _voice_reply_context_tool_result(
    *,
    request_id: Any,
    context: JsonObject,
    message_id: UUID,
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
            code="voice_message_not_in_context",
            message="Selected message is not in the scoped reply context",
        )

    result = await session.execute(
        select(Message, TelegramUpdate, GateEvent)
        .outerjoin(
            TelegramUpdate,
            (
                (TelegramUpdate.bot_id == Message.bot_id)
                & (TelegramUpdate.update_id == Message.telegram_update_id)
            ),
        )
        .outerjoin(GateEvent, GateEvent.id == Message.gate_event_id)
        .where(
            Message.id == message_id,
            Message.account_id.in_(principal.access_scope.account_ids),
            Message.project_id.in_(principal.access_scope.project_ids),
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.source == "telegram",
        )
    )
    row = result.one_or_none()
    if row is None:
        return _tool_error_result(
            request_id=request_id,
            code="voice_message_not_found",
            message="Selected outbound Telegram message was not found",
        )
    message, telegram_update, gate_event = row
    voice = _extract_voice_from_linked_payloads(
        telegram_update.payload if telegram_update is not None else None,
        gate_event.payload if gate_event is not None else None,
        message.payload,
    )
    if voice is None:
        voice = await _find_voice_by_exact_telegram_identity(
            session=session,
            message=message,
        )
    if voice is None:
        return _tool_error_result(
            request_id=request_id,
            code="selected_message_is_not_voice",
            message="Selected message has no retrievable Telegram audio reference",
        )
    if not settings.telegram_bot_token:
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_download_unavailable",
            message="Telegram voice download is not configured",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            downloaded = await TelegramVoiceDownloader(
                config=TelegramVoiceDownloadConfig(
                    bot_token=settings.telegram_bot_token,
                ),
                http=http_client,
            ).download(voice)
    except (TelegramVoiceError, ValueError):
        return _tool_error_result(
            request_id=request_id,
            code="telegram_voice_download_failed",
            message="Telegram voice download failed closed",
        )

    media_type = (
        downloaded.media_type
        if downloaded.media_type.startswith("audio/")
        else "audio/ogg"
    )
    return _voice_audio_tool_result(
        request_id=request_id,
        context=context,
        data=downloaded.data,
        media_type=media_type,
    )


def _extract_voice_from_linked_payloads(
    *payloads: JsonObject | None,
) -> TelegramVoiceReference | None:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        try:
            envelope = extract_telegram_voice(payload)
        except TelegramVoiceError:
            envelope = None
        if envelope is not None:
            return envelope.voice

        for message in _telegram_audio_messages(payload):
            for field in ("voice", "audio", "document"):
                raw_media = message.get(field)
                if not isinstance(raw_media, dict):
                    continue
                if field == "document":
                    mime_type = raw_media.get("mime_type")
                    if not isinstance(mime_type, str) or not mime_type.startswith(
                        "audio/"
                    ):
                        continue
                reference = _telegram_audio_reference(raw_media)
                if reference is not None:
                    return reference
    return None


async def _find_voice_by_exact_telegram_identity(
    *,
    session: AsyncSession,
    message: Message,
) -> TelegramVoiceReference | None:
    if message.telegram_message_id is None:
        return None

    event_result = await session.execute(
        select(GateEvent.payload)
        .where(
            GateEvent.bot_id == message.bot_id,
            GateEvent.chat_id == message.chat_id,
            GateEvent.telegram_message_id == message.telegram_message_id,
        )
        .order_by(GateEvent.received_at.desc())
        .limit(5)
    )
    voice = _extract_voice_from_linked_payloads(*event_result.scalars().all())
    if voice is not None:
        return voice

    anchor = message.sent_at or message.received_at or message.created_at
    update_result = await session.execute(
        select(TelegramUpdate.payload)
        .where(
            TelegramUpdate.bot_id == message.bot_id,
            TelegramUpdate.received_at >= anchor - timedelta(days=1),
            TelegramUpdate.received_at <= anchor + timedelta(days=1),
        )
        .order_by(TelegramUpdate.received_at.desc())
        .limit(1000)
    )
    for payload in update_result.scalars():
        if not isinstance(payload, dict):
            continue
        for raw_message in _telegram_audio_messages(payload):
            if _same_telegram_message(raw_message=raw_message, message=message):
                return _extract_voice_from_linked_payloads(raw_message)
    return None


def _same_telegram_message(*, raw_message: JsonObject, message: Message) -> bool:
    raw_chat = raw_message.get("chat")
    return (
        raw_message.get("message_id") == message.telegram_message_id
        and isinstance(raw_chat, dict)
        and str(raw_chat.get("id")) == message.chat_id
    )


def _telegram_audio_messages(payload: JsonObject) -> tuple[JsonObject, ...]:
    messages: list[JsonObject] = []
    for key in (
        "message",
        "business_message",
        "edited_message",
        "edited_business_message",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            messages.append(value)
    result = payload.get("result")
    if isinstance(result, dict):
        messages.append(result)
    if any(isinstance(payload.get(key), dict) for key in ("voice", "audio", "document")):
        messages.append(payload)
    return tuple(messages)


def _telegram_audio_reference(raw_media: JsonObject) -> TelegramVoiceReference | None:
    file_id = raw_media.get("file_id")
    file_unique_id = raw_media.get("file_unique_id")
    file_size = raw_media.get("file_size")
    duration = raw_media.get("duration")
    if (
        not isinstance(file_id, str)
        or not file_id.strip()
        or not isinstance(file_unique_id, str)
        or not file_unique_id.strip()
        or (file_size is not None and (type(file_size) is not int or file_size <= 0))
        or type(duration) is not int
        or duration <= 0
    ):
        return None
    try:
        return TelegramVoiceReference(
            file_id=file_id,
            file_unique_id=file_unique_id,
            declared_size_bytes=file_size,
            duration_seconds=duration,
        )
    except TelegramVoiceError:
        return None
