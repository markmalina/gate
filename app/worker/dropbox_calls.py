"""Durable, default-off Dropbox mobile-call intake worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.audio_summary import AUDIO_SUMMARY_MODEL, build_audio_summary
from app.application.telegram_voice_transcription import (
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    DownloadedVoice,
    TelegramVoiceOutcome,
    TelegramVoiceReference,
    TelegramVoiceTranscriptionService,
    TranscribeTelegramVoiceCommand,
)
from app.core.config import DROPBOX_CALL_INBOX_PATH, Settings
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer
from app.models.domain import (
    UNCLASSIFIED_PROJECT_ID,
    DropboxCallInboxState,
    PhoneCallRecording,
    TelegramVoiceArchive,
)

logger = logging.getLogger(__name__)

_DROPBOX_API = "https://api.dropboxapi.com/2"
_DROPBOX_CONTENT_API = "https://content.dropboxapi.com/2"
# Reuse the existing hard-limited Gate voice budget until billing scopes are generalized.
_CALL_TRANSCRIPTION_BUDGET_SCOPE_KIND = "telegram_connector"
_CALL_TRANSCRIPTION_BUDGET_SCOPE_REF = "telegram_business"
_SUPPORTED_SUFFIXES = {
    ".3gp",
    ".aac",
    ".amr",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
_AUDIO_MEDIA_TYPES = {
    ".3gp": "audio/3gpp",
    ".aac": "audio/aac",
    ".amr": "audio/amr",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


class DropboxCallWorkerError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _StoredCallDownloader:
    def __init__(
        self,
        recording: PhoneCallRecording | TelegramVoiceArchive,
    ) -> None:
        self._recording = recording

    async def download(self, _reference: TelegramVoiceReference) -> DownloadedVoice:
        return DownloadedVoice(
            data=self._recording.data,
            filename=self._recording.filename,
            media_type=self._recording.media_type,
        )


async def run_dropbox_call_worker_loop(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Poll Dropbox as webhook recovery and drain its durable delta cursor."""

    if not settings.gate_dropbox_call_intake_enabled:
        return
    _validate_settings(settings)
    async with httpx.AsyncClient(timeout=60.0) as http:
        while True:
            try:
                await process_dropbox_call_changes(
                    settings=settings,
                    session_factory=session_factory,
                    http=http,
                )
            except asyncio.CancelledError:
                raise
            except DropboxCallWorkerError as exc:
                await _mark_retry(
                    settings=settings,
                    session_factory=session_factory,
                    code=exc.code,
                )
                logger.warning("Dropbox call intake deferred: code=%s", exc.code)
            except Exception:
                await _mark_retry(
                    settings=settings,
                    session_factory=session_factory,
                    code="dropbox_call_worker_failed",
                )
                logger.exception("Dropbox call intake failed")
            await asyncio.sleep(settings.gate_dropbox_call_poll_seconds)


async def process_dropbox_call_changes(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> None:
    """Fetch and commit all currently visible deltas before advancing the cursor."""

    _validate_settings(settings)
    state = await _load_or_create_state(settings, session_factory)
    if state.next_attempt_at is not None and state.next_attempt_at > datetime.now(UTC):
        return

    access_token = await _refresh_access_token(settings, http)
    await _verify_account_id(settings, http, access_token)
    await _recover_authorized_telegram_voices(
        settings=settings,
        session_factory=session_factory,
        http=http,
    )
    await _resume_received_recordings(
        settings=settings,
        session_factory=session_factory,
        http=http,
    )
    await _summarize_ready_audio(session_factory=session_factory)
    cursor = None if state.backfill_version < 1 else state.cursor
    while True:
        payload = await _list_changes(
            settings=settings,
            http=http,
            access_token=access_token,
            cursor=cursor,
        )
        entries = payload.get("entries")
        next_cursor = payload.get("cursor")
        has_more = payload.get("has_more")
        if (
            not isinstance(entries, list)
            or not isinstance(next_cursor, str)
            or not isinstance(has_more, bool)
        ):
            raise DropboxCallWorkerError("dropbox_call_delta_invalid")
        for entry in entries:
            if _is_supported_file(entry):
                await _ingest_entry(
                    settings=settings,
                    session_factory=session_factory,
                    http=http,
                    access_token=access_token,
                    entry=entry,
                )
        cursor = next_cursor
        await _commit_cursor(
            settings=settings,
            session_factory=session_factory,
            cursor=cursor,
            pending=has_more,
        )
        if not has_more:
            await _summarize_ready_audio(session_factory=session_factory)
            return


def _validate_settings(settings: Settings) -> None:
    if settings.dropbox_call_inbox_path != DROPBOX_CALL_INBOX_PATH:
        raise DropboxCallWorkerError("dropbox_call_inbox_path_invalid")
    if not settings.dropbox_call_app_key:
        raise DropboxCallWorkerError("dropbox_call_app_key_missing")
    if not settings.dropbox_call_app_secret.get_secret_value():
        raise DropboxCallWorkerError("dropbox_call_app_secret_missing")
    if not settings.dropbox_call_refresh_token.get_secret_value():
        raise DropboxCallWorkerError("dropbox_call_refresh_token_missing")
    if not settings.dropbox_call_account_id:
        raise DropboxCallWorkerError("dropbox_call_account_id_missing")
    if not settings.openai_api_key.get_secret_value():
        raise DropboxCallWorkerError("dropbox_call_openai_key_missing")


async def _load_or_create_state(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> DropboxCallInboxState:
    async with session_factory() as session:
        state = await session.get(
            DropboxCallInboxState,
            settings.dropbox_call_account_id,
        )
        if state is None:
            state = DropboxCallInboxState(account_id=settings.dropbox_call_account_id)
            session.add(state)
            await session.commit()
            await session.refresh(state)
        return state


async def _refresh_access_token(
    settings: Settings,
    http: httpx.AsyncClient,
) -> str:
    try:
        response = await http.post(
            "https://api.dropboxapi.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.dropbox_call_refresh_token.get_secret_value(),
            },
            auth=(
                settings.dropbox_call_app_key,
                settings.dropbox_call_app_secret.get_secret_value(),
            ),
        )
    except httpx.HTTPError:
        raise DropboxCallWorkerError("dropbox_call_oauth_transport_failed") from None
    if response.is_error:
        raise DropboxCallWorkerError("dropbox_call_oauth_rejected")
    try:
        payload: Any = response.json()
    except ValueError:
        raise DropboxCallWorkerError("dropbox_call_oauth_invalid") from None
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise DropboxCallWorkerError("dropbox_call_oauth_invalid")
    return token


async def _verify_account_id(
    settings: Settings,
    http: httpx.AsyncClient,
    access_token: str,
) -> None:
    try:
        response = await http.post(
            f"{_DROPBOX_API}/users/get_current_account",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError:
        raise DropboxCallWorkerError("dropbox_call_identity_transport_failed") from None
    if response.is_error:
        raise DropboxCallWorkerError("dropbox_call_identity_rejected")
    try:
        payload: Any = response.json()
    except ValueError:
        raise DropboxCallWorkerError("dropbox_call_identity_invalid") from None
    account_id = payload.get("account_id") if isinstance(payload, dict) else None
    if account_id != settings.dropbox_call_account_id:
        raise DropboxCallWorkerError("dropbox_call_identity_mismatch")


async def _list_changes(
    *,
    settings: Settings,
    http: httpx.AsyncClient,
    access_token: str,
    cursor: str | None,
) -> dict[str, Any]:
    if cursor is None:
        endpoint = f"{_DROPBOX_API}/files/list_folder"
        body: dict[str, Any] = {
            "path": settings.dropbox_call_inbox_path,
            "recursive": True,
            "include_deleted": False,
            "include_non_downloadable_files": False,
        }
    else:
        endpoint = f"{_DROPBOX_API}/files/list_folder/continue"
        body = {"cursor": cursor}
    try:
        response = await http.post(
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
    except httpx.HTTPError:
        raise DropboxCallWorkerError("dropbox_call_delta_transport_failed") from None
    if response.is_error:
        raise DropboxCallWorkerError("dropbox_call_delta_rejected")
    try:
        payload: Any = response.json()
    except ValueError:
        raise DropboxCallWorkerError("dropbox_call_delta_invalid") from None
    if not isinstance(payload, dict):
        raise DropboxCallWorkerError("dropbox_call_delta_invalid")
    return payload


def _is_supported_file(entry: object) -> bool:
    if not isinstance(entry, dict) or entry.get(".tag") != "file":
        return False
    name = entry.get("name")
    path = entry.get("path_display")
    size = entry.get("size")
    return (
        isinstance(name, str)
        and isinstance(path, str)
        and _is_in_call_inbox(path)
        and PurePosixPath(name).suffix.lower() in _SUPPORTED_SUFFIXES
        and isinstance(size, int)
        and 0 < size <= TELEGRAM_DOWNLOAD_LIMIT_BYTES
    )


def _is_in_call_inbox(path: str) -> bool:
    return path.casefold().startswith(f"{DROPBOX_CALL_INBOX_PATH.casefold()}/")


async def _ingest_entry(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    access_token: str,
    entry: dict[str, Any],
) -> None:
    file_id = _required_string(entry, "id", "dropbox_call_file_id_invalid")
    revision = _required_string(entry, "rev", "dropbox_call_revision_invalid")
    name = _required_string(entry, "name", "dropbox_call_filename_invalid", maximum=255)
    path = _required_string(
        entry,
        "path_display",
        "dropbox_call_path_invalid",
        maximum=1024,
    )
    if not _is_in_call_inbox(path):
        raise DropboxCallWorkerError("dropbox_call_path_outside_inbox")
    size = entry.get("size")
    if not isinstance(size, int) or size <= 0 or size > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
        raise DropboxCallWorkerError("dropbox_call_size_invalid")

    async with session_factory() as session:
        duplicate = await session.scalar(
            select(PhoneCallRecording.id).where(
                PhoneCallRecording.source_account_id
                == settings.dropbox_call_account_id,
                PhoneCallRecording.source_file_id == file_id,
                PhoneCallRecording.source_revision == revision,
            )
        )
    if duplicate is not None:
        return

    data, media_type = await _download_revision(
        http=http,
        access_token=access_token,
        revision=revision,
        expected_size=size,
        fallback_name=name,
    )
    duration_seconds = await _probe_duration_seconds(data)
    now = datetime.now(UTC)
    recording = PhoneCallRecording(
        workspace_key=settings.gate_workspace_id,
        project_id=UNCLASSIFIED_PROJECT_ID,
        source_account_id=settings.dropbox_call_account_id,
        source_file_id=file_id,
        source_revision=revision,
        source_path=path,
        filename=name,
        media_type=media_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        duration_seconds=duration_seconds,
        received_at=now,
    )
    if duration_seconds is None:
        recording.transcription_status = TelegramVoiceOutcome.REJECTED.value
        recording.transcription_reason_code = "dropbox_call_duration_unknown"
        recording.transcript_language = "ru"
        recording.transcription_model = settings.gate_telegram_voice_transcription_model
        recording.transcribed_at = now
        async with session_factory() as session:
            session.add(recording)
            await session.commit()
        return

    async with session_factory() as session:
        session.add(recording)
        await session.commit()
        await session.refresh(recording)

    await _transcribe_recording(
        settings=settings,
        session_factory=session_factory,
        http=http,
        recording=recording,
    )


async def _resume_received_recordings(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> None:
    """Resume durable rows left incomplete by a prior worker failure or restart."""

    async with session_factory() as session:
        recording_ids = list(
            (
                await session.scalars(
                    select(PhoneCallRecording.id)
                    .where(
                        PhoneCallRecording.workspace_key == settings.gate_workspace_id,
                        PhoneCallRecording.source_account_id
                        == settings.dropbox_call_account_id,
                        PhoneCallRecording.transcription_status
                        == "received",
                    )
                    .order_by(PhoneCallRecording.received_at.asc())
                    .limit(10)
                )
            ).all()
        )
    for recording_id in recording_ids:
        async with session_factory() as session:
            recording = await session.get(PhoneCallRecording, recording_id)
        if recording is None or recording.transcription_status != "received":
            continue
        await _transcribe_recording(
            settings=settings,
            session_factory=session_factory,
            http=http,
            recording=recording,
        )


async def _transcribe_recording(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    recording: PhoneCallRecording,
) -> None:
    duration_seconds = recording.duration_seconds
    if duration_seconds is None:
        raise DropboxCallWorkerError("dropbox_call_duration_unknown")
    occurred_at = recording.received_at
    service = TelegramVoiceTranscriptionService(
        downloader=_StoredCallDownloader(recording),
        normalizer=TelegramVoiceNormalizer(),
        budget=SqlBillingBudgetService(session_factory),
        speech_to_text=OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(
                api_key=settings.openai_api_key.get_secret_value(),
                enabled=True,
                model=settings.gate_telegram_voice_transcription_model,
                base_url=settings.openai_api_base_url,
            ),
            http=http,
        ),
    )
    result = await service.transcribe(
        TranscribeTelegramVoiceCommand(
            voice=TelegramVoiceReference(
                file_id=recording.source_file_id,
                file_unique_id=recording.source_revision,
                declared_size_bytes=recording.size_bytes,
                duration_seconds=duration_seconds,
            ),
            workspace_key=settings.gate_workspace_id,
            connector_ref=_CALL_TRANSCRIPTION_BUDGET_SCOPE_REF,
            reservation_key=f"dropbox-call-v1:{recording.id}",
            period_key=occurred_at.strftime("%Y-%m"),
            requested_microusd=max(
                100,
                duration_seconds
                * settings.gate_telegram_voice_reserve_microusd_per_second,
            ),
            occurred_at=occurred_at,
            language="ru",
            enabled=True,
            budget_scope_kind=_CALL_TRANSCRIPTION_BUDGET_SCOPE_KIND,
        )
    )
    async with session_factory() as session:
        stored = await session.get(PhoneCallRecording, recording.id)
        if stored is None:
            raise DropboxCallWorkerError("dropbox_call_recording_missing")
        stored.transcription_status = result.outcome.value
        stored.transcript_text = result.alpha_input_text
        stored.transcript_language = "ru"
        stored.transcription_model = settings.gate_telegram_voice_transcription_model
        stored.transcription_reason_code = result.reason_code
        stored.transcribed_at = datetime.now(UTC)
        if result.alpha_input_text:
            stored.summary_status = "summarized"
            stored.summary_text = build_audio_summary(result.alpha_input_text)
            stored.summary_model = AUDIO_SUMMARY_MODEL
            stored.summary_reason_code = "audio_summary_generated"
            stored.summarized_at = datetime.now(UTC)
        await session.commit()


async def _recover_authorized_telegram_voices(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> None:
    """Retry only the three archives explicitly authorized by migration 0052."""

    async with session_factory() as session:
        archive_ids = list(
            (
                await session.scalars(
                    select(TelegramVoiceArchive.id)
                    .where(
                        TelegramVoiceArchive.transcription_recovery_authorized_at
                        .is_not(None),
                        or_(
                            TelegramVoiceArchive.transcription_status.is_(None),
                            TelegramVoiceArchive.transcription_status
                            == TelegramVoiceOutcome.UNKNOWN.value,
                        ),
                    )
                    .order_by(TelegramVoiceArchive.received_at.asc())
                    .limit(3)
                )
            ).all()
        )
    for archive_id in archive_ids:
        async with session_factory() as session:
            archive = await session.get(TelegramVoiceArchive, archive_id)
        if archive is None or archive.transcription_recovery_authorized_at is None:
            continue
        now = datetime.now(UTC)
        service = TelegramVoiceTranscriptionService(
            downloader=_StoredCallDownloader(archive),
            normalizer=TelegramVoiceNormalizer(),
            budget=SqlBillingBudgetService(session_factory),
            speech_to_text=OpenAITranscriptionClient(
                config=OpenAITranscriptionConfig(
                    api_key=settings.openai_api_key.get_secret_value(),
                    enabled=True,
                    model=settings.gate_telegram_voice_transcription_model,
                    base_url=settings.openai_api_base_url,
                ),
                http=http,
            ),
        )
        result = await service.transcribe(
            TranscribeTelegramVoiceCommand(
                voice=TelegramVoiceReference(
                    file_id=str(archive.id),
                    file_unique_id=archive.sha256,
                    declared_size_bytes=archive.size_bytes,
                    duration_seconds=archive.duration_seconds,
                ),
                workspace_key=settings.gate_workspace_id,
                connector_ref=_CALL_TRANSCRIPTION_BUDGET_SCOPE_REF,
                reservation_key=f"telegram-voice-recovery-v1:{archive.id}",
                period_key=now.strftime("%Y-%m"),
                requested_microusd=max(
                    100,
                    archive.duration_seconds
                    * settings.gate_telegram_voice_reserve_microusd_per_second,
                ),
                occurred_at=now,
                language="ru",
                enabled=True,
                budget_scope_kind=_CALL_TRANSCRIPTION_BUDGET_SCOPE_KIND,
            )
        )
        async with session_factory() as session:
            stored = await session.get(
                TelegramVoiceArchive,
                archive.id,
                with_for_update=True,
            )
            if stored is None:
                raise DropboxCallWorkerError("telegram_voice_recovery_archive_missing")
            stored.transcription_status = result.outcome.value
            stored.transcript_text = result.alpha_input_text
            stored.transcript_language = "ru"
            stored.transcription_model = settings.gate_telegram_voice_transcription_model
            stored.transcription_reason_code = result.reason_code
            stored.transcribed_at = now
            stored.transcription_recovery_authorized_at = None
            if result.alpha_input_text:
                stored.summary_status = "summarized"
                stored.summary_text = build_audio_summary(result.alpha_input_text)
                stored.summary_model = AUDIO_SUMMARY_MODEL
                stored.summary_reason_code = "audio_summary_generated"
                stored.summarized_at = now
            await session.commit()


async def _summarize_ready_audio(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Backfill bounded summaries for successful stored transcripts."""

    async with session_factory() as session:
        voice_rows = list(
            (
                await session.scalars(
                    select(TelegramVoiceArchive)
                    .where(
                        TelegramVoiceArchive.transcription_status
                        == TelegramVoiceOutcome.TRANSCRIBED.value,
                        TelegramVoiceArchive.transcript_text.is_not(None),
                        TelegramVoiceArchive.summary_status.is_(None),
                    )
                    .order_by(TelegramVoiceArchive.received_at.asc())
                    .limit(10)
                )
            ).all()
        )
        call_rows = list(
            (
                await session.scalars(
                    select(PhoneCallRecording)
                    .where(
                        PhoneCallRecording.transcription_status
                        == TelegramVoiceOutcome.TRANSCRIBED.value,
                        PhoneCallRecording.transcript_text.is_not(None),
                        PhoneCallRecording.summary_status.is_(None),
                    )
                    .order_by(PhoneCallRecording.received_at.asc())
                    .limit(10)
                )
            ).all()
        )
        now = datetime.now(UTC)
        for archive in voice_rows:
            if archive.transcript_text is None:
                continue
            archive.summary_status = "summarized"
            archive.summary_text = build_audio_summary(archive.transcript_text)
            archive.summary_model = AUDIO_SUMMARY_MODEL
            archive.summary_reason_code = "audio_summary_generated"
            archive.summarized_at = now
        for recording in call_rows:
            if recording.transcript_text is None:
                continue
            recording.summary_status = "summarized"
            recording.summary_text = build_audio_summary(recording.transcript_text)
            recording.summary_model = AUDIO_SUMMARY_MODEL
            recording.summary_reason_code = "audio_summary_generated"
            recording.summarized_at = now
        await session.commit()


async def _download_revision(
    *,
    http: httpx.AsyncClient,
    access_token: str,
    revision: str,
    expected_size: int,
    fallback_name: str,
) -> tuple[bytes, str]:
    try:
        data = bytearray()
        async with http.stream(
            "POST",
            f"{_DROPBOX_CONTENT_API}/files/download",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Dropbox-API-Arg": json.dumps({"path": f"rev:{revision}"}),
            },
        ) as response:
            if response.is_error:
                raise DropboxCallWorkerError("dropbox_call_download_rejected")
            media_type = response.headers.get("content-type", "").split(";", 1)[0]
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
                    raise DropboxCallWorkerError("dropbox_call_download_limit_exceeded")
    except httpx.HTTPError:
        raise DropboxCallWorkerError("dropbox_call_download_transport_failed") from None
    if (
        not data
        or len(data) != expected_size
        or len(data) > TELEGRAM_DOWNLOAD_LIMIT_BYTES
    ):
        raise DropboxCallWorkerError("dropbox_call_download_size_mismatch")
    if not media_type.startswith("audio/"):
        media_type = _audio_media_type(fallback_name)
    return bytes(data), media_type


def _audio_media_type(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    return (
        _AUDIO_MEDIA_TYPES.get(suffix)
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )


async def _probe_duration_seconds(data: bytes) -> int | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "pipe:0",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    stdout, _ = await process.communicate(data)
    if process.returncode != 0:
        return None
    try:
        duration = float(stdout.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0 or duration > 24 * 60 * 60:
        return None
    return max(1, math.ceil(duration))


async def _commit_cursor(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    cursor: str,
    pending: bool,
) -> None:
    async with session_factory() as session:
        state = await session.get(
            DropboxCallInboxState,
            settings.dropbox_call_account_id,
            with_for_update=True,
        )
        if state is None:
            raise DropboxCallWorkerError("dropbox_call_state_missing")
        state.cursor = cursor
        state.backfill_version = 1
        state.pending = pending
        state.attempt_count = 0
        state.next_attempt_at = None
        state.last_error_code = None
        await session.commit()


async def _mark_retry(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    code: str,
) -> None:
    async with session_factory() as session:
        state = await session.get(
            DropboxCallInboxState,
            settings.dropbox_call_account_id,
            with_for_update=True,
        )
        if state is None:
            state = DropboxCallInboxState(account_id=settings.dropbox_call_account_id)
            session.add(state)
        state.pending = True
        state.attempt_count += 1
        delay = min(300, 2 ** min(state.attempt_count, 8))
        state.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
        state.last_error_code = code
        await session.commit()


def _required_string(
    payload: dict[str, Any],
    key: str,
    code: str,
    *,
    maximum: int = 255,
) -> str:
    value = payload.get(key)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise DropboxCallWorkerError(code)
    return value
