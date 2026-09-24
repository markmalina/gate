"""Durable, default-off Dropbox mobile-call intake worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import mimetypes
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.audio_summary import AUDIO_SUMMARY_MODEL, build_audio_summary
from app.application.telegram_voice_transcription import (
    MAX_SOURCE_AUDIO_BYTES,
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
from app.infrastructure.owner_voice_recovery import (
    claim_owner_voice_recovery,
    owner_voice_reservation_key,
    reconcile_exact_call_copies,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer
from app.models.billing import BillingBudgetReservationModel
from app.models.domain import (
    UNCLASSIFIED_PROJECT_ID,
    DropboxCallInboxState,
    PhoneCallRecording,
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
    TelegramVoiceArchive,
)

logger = logging.getLogger(__name__)

_DROPBOX_API = "https://api.dropboxapi.com/2"
_DROPBOX_CONTENT_API = "https://content.dropboxapi.com/2"
DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES = MAX_SOURCE_AUDIO_BYTES
# Reuse the existing hard-limited Gate voice budget until billing scopes are generalized.
_CALL_TRANSCRIPTION_BUDGET_SCOPE_KIND = "telegram_connector"
_CALL_TRANSCRIPTION_BUDGET_SCOPE_REF = "telegram_business"
_LONG_CALL_RECOVERY_STATUS = "recovery_v13"
_LONG_CALL_RECOVERY_MODEL = "gpt-4o-transcribe"
_CHUNK_REPAIR_RECOVERY_STATUS = "recovery_v14"
_CHUNK_REPAIR_PROCESSING = "dropbox_call_processing_v14"
_EMPTY_RESULT_RECOVERY_STATUS = "recovery_v15"
_EMPTY_RESULT_PROCESSING = "dropbox_call_processing_v15"
_CALL_BACKFILL_VERSION = 2
_MAX_CALL_DURATION_SECONDS = 60 * 60
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
    await _recover_duration_unknown_recordings(
        settings=settings, session_factory=session_factory,
    )
    await _resume_received_recordings(
        settings=settings,
        session_factory=session_factory,
        http=http,
    )
    await _summarize_ready_audio(session_factory=session_factory)
    await _mark_superseded_revisions(
        settings=settings, session_factory=session_factory,
    )
    await reconcile_exact_call_copies(
        session_factory, workspace_key=settings.gate_workspace_id,
    )
    cursor = None if state.backfill_version < _CALL_BACKFILL_VERSION else state.cursor
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
            if _is_oversize_audio_file(entry):
                # The old bounded-file filter remains strict; this separate
                # rejection prevents an oversized supported source from being
                # silently skipped when the delta cursor advances.
                raise DropboxCallWorkerError("dropbox_call_download_limit_exceeded")
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
            await _mark_superseded_revisions(
                settings=settings, session_factory=session_factory,
            )
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


def _is_oversize_audio_file(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get(".tag") == "file"
        and isinstance(entry.get("name"), str)
        and isinstance(entry.get("path_display"), str)
        and _is_in_call_inbox(entry["path_display"])
        and PurePosixPath(entry["name"]).suffix.lower() in _SUPPORTED_SUFFIXES
        and isinstance(entry.get("size"), int)
        and entry["size"] > DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES
    )


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
        and 0 < size <= DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES
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
    if not isinstance(size, int) or size <= 0:
        raise DropboxCallWorkerError("dropbox_call_size_invalid")
    if size > DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES:
        # Keep the delta cursor before this entry; the owner sees the precise
        # reason in intake status instead of silently losing this revision.
        raise DropboxCallWorkerError("dropbox_call_download_limit_exceeded")

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
    if duration_seconds > _MAX_CALL_DURATION_SECONDS:
        recording.transcription_status = TelegramVoiceOutcome.REJECTED.value
        recording.transcription_reason_code = "dropbox_call_duration_exceeded"
        recording.transcript_language = "ru"
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


async def _recover_duration_unknown_recordings(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Re-probe archived bytes with no prior provider reservation, once each."""

    async with session_factory() as session:
        candidates = list((await session.scalars(
            select(PhoneCallRecording)
            .where(
                PhoneCallRecording.workspace_key == settings.gate_workspace_id,
                PhoneCallRecording.source_account_id == settings.dropbox_call_account_id,
                PhoneCallRecording.transcription_status == "rejected",
                PhoneCallRecording.transcription_reason_code.in_((
                    "dropbox_call_duration_unknown",
                    "dropbox_call_duration_probe_failed",
                )),
                PhoneCallRecording.duration_seconds.is_(None),
                PhoneCallRecording.transcript_text.is_(None),
            )
            .order_by(PhoneCallRecording.received_at.asc()).limit(10)
        )).all())
    for candidate in candidates:
        data = candidate.data
        valid = (
            0 < candidate.size_bytes <= DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES
            and len(data) == candidate.size_bytes
            and hashlib.sha256(data).hexdigest() == candidate.sha256
        )
        duration = await _probe_duration_seconds(data) if valid else None
        async with session_factory() as session:
            row = await session.scalar(
                select(PhoneCallRecording)
                .where(PhoneCallRecording.id == candidate.id)
                .with_for_update(skip_locked=True)
            )
            if (
                row is None or row.workspace_key != settings.gate_workspace_id
                or row.source_account_id != settings.dropbox_call_account_id
                or row.source_file_id != candidate.source_file_id
                or row.source_revision != candidate.source_revision
                or row.sha256 != candidate.sha256
                or row.transcription_status != "rejected"
                or row.transcription_reason_code not in (
                    "dropbox_call_duration_unknown",
                    "dropbox_call_duration_probe_failed",
                )
                or row.duration_seconds is not None or row.transcript_text is not None
            ):
                continue
            prior = await session.scalar(
                select(BillingBudgetReservationModel.id)
                .where(BillingBudgetReservationModel.reservation_key.like(
                    f"dropbox-call-%:{row.id}"
                ))
                .limit(1)
            )
            if prior is not None:
                row.transcription_reason_code = "dropbox_call_recovery_prior_reservation"
            elif not valid:
                row.transcription_reason_code = "dropbox_call_archive_integrity_failed"
            elif duration is None:
                row.transcription_reason_code = "dropbox_call_media_unreadable"
            elif duration > _MAX_CALL_DURATION_SECONDS:
                row.duration_seconds = duration
                row.transcription_reason_code = "dropbox_call_duration_exceeded"
            else:
                row.duration_seconds = duration
                row.transcription_status = "received"
                row.transcription_reason_code = "dropbox_call_duration_recovered"
                row.transcribed_at = None
            await session.commit()


async def _mark_superseded_revisions(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Close unreadable partial uploads only after the same file has a later transcript."""

    async with session_factory() as session:
        candidates = list((await session.scalars(
            select(PhoneCallRecording.id)
            .where(
                PhoneCallRecording.workspace_key == settings.gate_workspace_id,
                PhoneCallRecording.source_account_id == settings.dropbox_call_account_id,
                PhoneCallRecording.transcription_status == "rejected",
                PhoneCallRecording.transcription_reason_code
                == "dropbox_call_media_unreadable",
                PhoneCallRecording.transcript_text.is_(None),
            )
            .order_by(PhoneCallRecording.received_at.asc()).limit(20)
        )).all())
    for candidate_id in candidates:
        async with session_factory() as session:
            row = await session.scalar(
                select(PhoneCallRecording)
                .where(PhoneCallRecording.id == candidate_id)
                .with_for_update(skip_locked=True)
            )
            if (
                row is None or row.workspace_key != settings.gate_workspace_id
                or row.source_account_id != settings.dropbox_call_account_id
                or row.transcription_status != "rejected"
                or row.transcription_reason_code != "dropbox_call_media_unreadable"
                or row.transcript_text is not None
            ):
                continue
            successor_id = await session.scalar(
                select(PhoneCallRecording.id)
                .where(
                    PhoneCallRecording.workspace_key == row.workspace_key,
                    PhoneCallRecording.project_id == row.project_id,
                    PhoneCallRecording.source_account_id == row.source_account_id,
                    PhoneCallRecording.source_file_id == row.source_file_id,
                    PhoneCallRecording.source_revision != row.source_revision,
                    PhoneCallRecording.source_path == row.source_path,
                    PhoneCallRecording.filename == row.filename,
                    PhoneCallRecording.received_at > row.received_at,
                    PhoneCallRecording.size_bytes > row.size_bytes,
                    PhoneCallRecording.transcription_status == "transcribed",
                    PhoneCallRecording.transcript_text.is_not(None),
                )
                .order_by(PhoneCallRecording.received_at.desc()).limit(1)
            )
            if successor_id is None:
                continue
            row.transcription_reason_code = "dropbox_call_revision_superseded"
            await session.commit()


async def _resume_received_recordings(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> None:
    """Resume durable rows left incomplete by a prior worker failure or restart."""

    stale_before = datetime.now(UTC) - timedelta(hours=1)
    async with session_factory() as session:
        recording_ids = list(
            (
                await session.scalars(
                    select(PhoneCallRecording.id)
                    .where(
                        PhoneCallRecording.workspace_key == settings.gate_workspace_id,
                        PhoneCallRecording.source_account_id
                        == settings.dropbox_call_account_id,
                        or_(
                            PhoneCallRecording.transcription_status == "received",
                            PhoneCallRecording.transcription_status
                            == _LONG_CALL_RECOVERY_STATUS,
                            PhoneCallRecording.transcription_status
                            == _CHUNK_REPAIR_RECOVERY_STATUS,
                            PhoneCallRecording.transcription_status
                            == _EMPTY_RESULT_RECOVERY_STATUS,
                            and_(
                                PhoneCallRecording.transcription_status == "processing",
                                PhoneCallRecording.transcribed_at < stale_before,
                            ),
                        ),
                    )
                    .order_by(PhoneCallRecording.received_at.asc())
                    .limit(10)
                )
            ).all()
        )
    for recording_id in recording_ids:
        async with session_factory() as session:
            recording = await session.get(PhoneCallRecording, recording_id)
        if recording is None:
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
    claimed_at = datetime.now(UTC)
    stale_before = claimed_at - timedelta(hours=1)
    async with session_factory() as session:
        claimed = await session.scalar(
            select(PhoneCallRecording)
            .where(
                PhoneCallRecording.id == recording.id,
                or_(
                    PhoneCallRecording.transcription_status == "received",
                    PhoneCallRecording.transcription_status
                    == _LONG_CALL_RECOVERY_STATUS,
                    PhoneCallRecording.transcription_status
                    == _CHUNK_REPAIR_RECOVERY_STATUS,
                    PhoneCallRecording.transcription_status
                    == _EMPTY_RESULT_RECOVERY_STATUS,
                    and_(
                        PhoneCallRecording.transcription_status == "processing",
                        PhoneCallRecording.transcribed_at < stale_before,
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
        )
        if claimed is None:
            return
        transcription_model = (
            _LONG_CALL_RECOVERY_MODEL
            if claimed.transcription_status == _LONG_CALL_RECOVERY_STATUS
            else settings.gate_telegram_voice_transcription_model
        )
        chunk_recovery = (
            claimed.transcription_status == _CHUNK_REPAIR_RECOVERY_STATUS
            or claimed.transcription_reason_code == _CHUNK_REPAIR_PROCESSING
        )
        empty_result_recovery = (
            claimed.transcription_status == _EMPTY_RESULT_RECOVERY_STATUS
            or claimed.transcription_reason_code == _EMPTY_RESULT_PROCESSING
        )
        duration_recovery = claimed.transcription_reason_code == "dropbox_call_duration_recovered"
        claimed.transcription_status = "processing"
        if empty_result_recovery:
            claimed.transcription_reason_code = _EMPTY_RESULT_PROCESSING
        elif chunk_recovery:
            claimed.transcription_reason_code = _CHUNK_REPAIR_PROCESSING
        else:
            claimed.transcription_reason_code = "dropbox_call_processing"
        claimed.transcribed_at = claimed_at
        await session.commit()
        await session.refresh(claimed)
        recording = claimed

    duration_seconds = recording.duration_seconds
    if duration_seconds is None:
        raise DropboxCallWorkerError("dropbox_call_duration_unknown")
    occurred_at = (
        claimed_at if chunk_recovery or empty_result_recovery or duration_recovery
        else recording.received_at
    )
    service = TelegramVoiceTranscriptionService(
        downloader=_StoredCallDownloader(recording),
        normalizer=TelegramVoiceNormalizer(),
        budget=SqlBillingBudgetService(session_factory),
        speech_to_text=OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(
                api_key=settings.openai_api_key.get_secret_value(),
                enabled=True,
                model=transcription_model,
                base_url=settings.openai_api_base_url,
            ),
            http=http,
        ),
        download_limit_bytes=DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES,
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
            reservation_key=(
                f"dropbox-call-v15:{recording.id}" if empty_result_recovery else (
                    f"dropbox-call-v14:{recording.id}"
                    if chunk_recovery else f"dropbox-call-v13:{recording.id}"
                )
            ),
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
        stored.transcription_model = transcription_model
        stored.transcription_reason_code = result.reason_code
        stored.transcribed_at = datetime.now(UTC)
        if result.alpha_input_text:
            stored.summary_status = "summarized"
            stored.summary_text = build_audio_summary(result.alpha_input_text)
            stored.summary_model = AUDIO_SUMMARY_MODEL
            stored.summary_reason_code = "audio_summary_generated"
            stored.summarized_at = datetime.now(UTC)
        elif result.reason_code == "telegram_voice_no_speech":
            _store_no_speech_summary(stored)
        await session.commit()


_TELEGRAM_VOICE_RECOVERY_GENERATION = 2


def _store_no_speech_summary(archive: PhoneCallRecording | TelegramVoiceArchive) -> None:
    archive.summary_status = "summarized"
    archive.summary_text = "Распознавание завершено: речь не распознана."
    archive.summary_model = AUDIO_SUMMARY_MODEL
    archive.summary_reason_code = "audio_no_speech"
    archive.summarized_at = datetime.now(UTC)


async def _claim_authorized_telegram_voice(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[TelegramVoiceArchive, int] | None:
    """Atomically consume one exact dead-letter recovery grant before provider I/O."""

    expected_error = "openai_transcription_transport_unknown"
    async with session_factory() as session:
        archives = list(
            (
                await session.scalars(
                    select(TelegramVoiceArchive)
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == TelegramVoiceArchive.receipt_id,
                    )
                    .join(
                        TelegramOutboxEvent,
                        TelegramOutboxEvent.receipt_id == TelegramReceipt.id,
                    )
                    .where(
                        TelegramVoiceArchive.transcription_recovery_generation
                        == _TELEGRAM_VOICE_RECOVERY_GENERATION,
                        TelegramVoiceArchive.transcription_recovery_authorized_at
                        .is_(None),
                        TelegramVoiceArchive.transcription_status
                        == TelegramVoiceOutcome.UNKNOWN.value,
                        TelegramVoiceArchive.transcript_text.is_(None),
                        TelegramVoiceArchive.summary_text.is_(None),
                        TelegramReceipt.bot_key == "telegram_business",
                        TelegramReceipt.status
                        == TelegramReceiptStatus.DEAD_LETTER.value,
                        TelegramReceipt.last_error_code == expected_error,
                        TelegramOutboxEvent.status
                        == TelegramOutboxStatus.DEAD_LETTER.value,
                        TelegramOutboxEvent.last_error_code == expected_error,
                    )
                    .order_by(TelegramVoiceArchive.received_at.asc())
                    .limit(2)
                    .with_for_update(
                        of=(TelegramVoiceArchive,),
                        skip_locked=True,
                    )
                )
            ).all()
        )
        if len(archives) > 1:
            raise DropboxCallWorkerError(
                "telegram_voice_recovery_scope_ambiguous"
            )
        if not archives:
            return None
        archive = archives[0]
        generation = archive.transcription_recovery_generation
        if generation != _TELEGRAM_VOICE_RECOVERY_GENERATION:
            return None
        if (
            not archive.data
            or archive.size_bytes <= 0
            or archive.duration_seconds <= 0
            or len(archive.sha256) != 64
        ):
            raise DropboxCallWorkerError(
                "telegram_voice_recovery_archive_invalid"
            )

        # Commit the generation consumption before provider I/O. A crash now
        # fails closed: a new owner authorization is required for another try.
        archive.transcription_recovery_generation = None
        await session.commit()
        await session.refresh(archive)
        return archive, generation


def _telegram_voice_recovery_reservation_key(
    archive_id: object,
    generation: int,
) -> str:
    """Bind one recovery reservation to one isolated authorization generation."""

    if generation != _TELEGRAM_VOICE_RECOVERY_GENERATION:
        raise DropboxCallWorkerError(
            "telegram_voice_recovery_generation_invalid"
        )
    return f"telegram-voice-recovery-v{generation}:{archive_id}"


async def _recover_authorized_telegram_voices(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> None:
    """Consume and run at most one exact transport-unknown recovery grant."""

    claimed = await _claim_authorized_telegram_voice(
        session_factory=session_factory,
    )
    archive_only = claimed is None
    if archive_only:
        owner_archive = await claim_owner_voice_recovery(
            session_factory, workspace_key=settings.gate_workspace_id,
        )
        if owner_archive is None:
            return
        archive, generation = owner_archive, 5
    else:
        assert claimed is not None
        archive, generation = claimed
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
            reservation_key=(
                owner_voice_reservation_key(archive.id) if archive_only
                else _telegram_voice_recovery_reservation_key(archive.id, generation)
            ),
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
            raise DropboxCallWorkerError(
                "telegram_voice_recovery_archive_missing"
            )
        stored.transcription_status = result.outcome.value
        stored.transcript_text = result.alpha_input_text
        stored.transcript_language = "ru"
        stored.transcription_model = settings.gate_telegram_voice_transcription_model
        stored.transcription_reason_code = result.reason_code
        stored.transcribed_at = now
        if result.alpha_input_text:
            stored.summary_status = "summarized"
            stored.summary_text = build_audio_summary(result.alpha_input_text)
            stored.summary_model = AUDIO_SUMMARY_MODEL
            stored.summary_reason_code = "audio_summary_generated"
            stored.summarized_at = now
        elif result.reason_code == "telegram_voice_no_speech":
            _store_no_speech_summary(stored)
        if result.outcome is TelegramVoiceOutcome.TRANSCRIBED and not archive_only:
            await _requeue_recovered_telegram_voice(
                session=session,
                archive=stored,
                now=now,
            )
        await session.commit()


async def _requeue_recovered_telegram_voice(
    *,
    session: AsyncSession,
    archive: TelegramVoiceArchive,
    now: datetime,
) -> None:
    """Requeue only the exact dead letter whose archived transcript recovered."""

    row = (
        await session.execute(
            select(TelegramReceipt, TelegramOutboxEvent)
            .join(
                TelegramOutboxEvent,
                TelegramOutboxEvent.receipt_id == TelegramReceipt.id,
            )
            .where(TelegramReceipt.id == archive.receipt_id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return
    receipt, event = row
    expected_error = "openai_transcription_transport_unknown"
    if (
        receipt.status != TelegramReceiptStatus.DEAD_LETTER.value
        or event.status != TelegramOutboxStatus.DEAD_LETTER.value
        or receipt.last_error_code != expected_error
        or event.last_error_code != expected_error
    ):
        return

    event.status = TelegramOutboxStatus.PENDING.value
    event.attempt_count = 0
    event.available_at = now
    event.locked_at = None
    event.delivered_at = None
    event.last_error_code = None
    event.last_error_at = None

    receipt.status = TelegramReceiptStatus.RECEIVED.value
    receipt.attempt_count = 0
    receipt.processing_started_at = None
    receipt.processed_at = None
    receipt.next_attempt_at = None
    receipt.last_error_code = None
    receipt.last_error_at = None


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
                if len(data) > DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES:
                    raise DropboxCallWorkerError("dropbox_call_download_limit_exceeded")
    except httpx.HTTPError:
        raise DropboxCallWorkerError("dropbox_call_download_transport_failed") from None
    if (
        not data
        or len(data) != expected_size
        or len(data) > DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES
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
    if not data or len(data) > DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES:
        return None
    # MP4/M4A may keep its moov atom at the end. A pipe is not seekable, so
    # probing identical bytes through pipe:0 can return no duration.
    with tempfile.TemporaryDirectory(prefix="gate-call-probe-") as directory:
        source = Path(directory) / "source.m4a"
        await asyncio.to_thread(source.write_bytes, data)
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(source),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            process = None
        if process is not None:
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
            except (TimeoutError, asyncio.CancelledError) as exc:
                process.kill()
                await process.wait()
                if isinstance(exc, asyncio.CancelledError):
                    raise
            else:
                if process.returncode == 0:
                    try:
                        duration = float(stdout.decode("ascii").strip())
                    except (UnicodeDecodeError, ValueError):
                        pass
                    else:
                        if math.isfinite(duration) and 0 < duration <= 24 * 60 * 60:
                            return max(1, math.ceil(duration))

        # Some files contain decodable audio but no container duration. Decode
        # only to a null sink; progress reports the decoded audio end timestamp.
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-xerror", "-i", str(source), "-map", "0:a:0", "-vn",
                "-f", "null", "-progress", "pipe:1", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return None
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
        except (TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None
        if process.returncode != 0:
            return None
        timestamps = [
            line.removeprefix(b"out_time_us=")
            for line in stdout.splitlines()
            if line.startswith(b"out_time_us=")
        ]
        try:
            duration_us = int(timestamps[-1])
        except (IndexError, ValueError):
            return None
        if not 0 < duration_us <= 24 * 60 * 60 * 1_000_000:
            return None
        return max(1, math.ceil(duration_us / 1_000_000))


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
        state.backfill_version = _CALL_BACKFILL_VERSION
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
