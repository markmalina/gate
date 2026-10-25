"""Default-off owner watch intake. Separate cursor, originals and durable Mark outbox."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.billing import (
    BudgetAdmission,
    BudgetReservation,
    ReserveBudgetCommand,
    UsageUnit,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    NormalizedVoice,
    TelegramVoiceError,
    TelegramVoiceOutcome,
    TelegramVoiceReference,
    TelegramVoiceTranscriptionService,
    TranscribeTelegramVoiceCommand,
)
from app.core.config import Settings
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer
from app.infrastructure.watch_voice_github import (
    GithubReceipt,
    MarkInboxClient,
    WatchVoiceError,
    marker,
    transcript_parts,
)
from app.infrastructure.watch_voice_metrics import OBSERVATION, observed_cycle
from app.models.watch_voice import WatchVoiceCursor, WatchVoiceNote
from app.worker.dropbox_calls import (
    _audio_media_type,
    _download_revision,
    _probe_duration_seconds,
    _refresh_access_token,
    _verify_account_id,
)

PATH = "/Alpha/Voice"
SOURCE_LIMIT = 25 * 1024 * 1024
MAX_DURATION = 60 * 60
logger = logging.getLogger(__name__)


def validate_settings(settings: Settings) -> None:
    if not settings.gate_watch_voice_data_gate_approved:
        raise WatchVoiceError("watch_voice_data_gate_required")
    if (
        settings.gate_workspace_id != settings.gate_watch_voice_workspace_key
        or not settings.gate_watch_voice_workspace_key
    ):
        raise WatchVoiceError("watch_voice_workspace_mismatch")
    if (
        not settings.dropbox_call_app_key
        or not settings.dropbox_call_app_secret.get_secret_value()
        or not settings.dropbox_call_refresh_token.get_secret_value()
        or not settings.dropbox_call_account_id
    ):
        raise WatchVoiceError("watch_voice_dropbox_credentials_missing")
    if not settings.openai_api_key.get_secret_value():
        raise WatchVoiceError("watch_voice_transcription_key_missing")
    if (
        settings.openai_api_base_url.rstrip("/") != "https://api.openai.com/v1"
        or settings.gate_telegram_voice_transcription_model != "gpt-4o-mini-transcribe"
    ):
        raise WatchVoiceError("watch_voice_pinned_provider_mismatch")
    if (
        not settings.gate_mark_github_token.get_secret_value()
        or not settings.gate_mark_github_writer
    ):
        raise WatchVoiceError("watch_voice_github_credentials_missing")
    if bool(settings.gate_watch_voice_file_id) != bool(settings.gate_watch_voice_revision):
        raise WatchVoiceError("watch_voice_exact_source_pair_required")


def source_entry(entry: object) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get(".tag") != "file":
        return None
    path, name = entry.get("path_display"), entry.get("name")
    if not isinstance(path, str) or not path.casefold().startswith(PATH.casefold() + "/"):
        return None
    if not isinstance(name, str) or PurePosixPath(name).suffix.lower() not in {
        ".m4a",
        ".mp4",
        ".mp3",
        ".wav",
        ".ogg",
        ".webm",
        ".flac",
    }:
        return None
    if len(path) > 1024 or len(name) > 255:
        raise WatchVoiceError("watch_voice_source_name_limit")
    if type(entry.get("size")) is not int or not 0 < entry["size"] <= SOURCE_LIMIT:
        raise WatchVoiceError("watch_voice_source_size_limit")
    for key in ("id", "rev", "server_modified"):
        if not isinstance(entry.get(key), str) or not entry[key] or len(entry[key]) > 255:
            raise WatchVoiceError("watch_voice_source_metadata_invalid")
    return entry


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WatchVoiceError("watch_voice_source_time_invalid") from None
    if result.tzinfo is None:
        raise WatchVoiceError("watch_voice_source_time_invalid")
    return result.astimezone(UTC)


def note_scope(settings: Settings) -> tuple[Any, ...]:
    scope: tuple[Any, ...] = (
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
    )
    if settings.gate_watch_voice_file_id:
        scope += (
            WatchVoiceNote.file_id == settings.gate_watch_voice_file_id,
            WatchVoiceNote.revision == settings.gate_watch_voice_revision,
        )
    return scope


def future_cutover(state: WatchVoiceCursor | None) -> datetime:
    """Reuse durable Actions admission; the ordinary worker never creates it."""
    if state is None or not state.cursor or state.future_cutover_at is None:
        raise WatchVoiceError("watch_voice_future_cutover_required")
    value = state.future_cutover_at
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def future_entry(entry: dict[str, Any], cutover: datetime) -> bool:
    """Exclude Dropbox history before downloading or probing original audio."""
    return timestamp(entry["server_modified"]) > cutover


def future_note_scope(cutover: datetime) -> tuple[Any, ...]:
    """A late archived historical source is not a newly admitted recording."""
    return (
        WatchVoiceNote.received_at >= cutover,
        WatchVoiceNote.dropbox_modified_at > cutover,
    )


def issue_body(note: WatchVoiceNote, part: int) -> str:
    parts = transcript_parts(note.transcript or "")
    transcribed = note.transcribed_at.isoformat() if note.transcribed_at else "неизвестно"
    start = marker(note.id, part)
    if part:
        return f"{start}\n\nРасшифровка, часть {part + 1}/{len(parts)}:\n\n{parts[part]}"
    return (
        f"{start}\n\nАвтор: Марк Малиновский. Доставщик: Gate.\n"
        "Канал: Galaxy Watch → Samsung Звукозапись → Dropsync → Dropbox Voice.\n\n"
        f"Gate receipt / event_id: `{note.id}`\n"
        f"Оригинал: `watch-voice:{note.id}` (защищённый архив Gate).\n"
        f"Dropbox source: `{note.file_id}`, revision `{note.revision}`.\n"
        f"SHA-256 оригинала: `{note.sha256}`.\n"
        f"Размер: {note.size_bytes} bytes.\n"
        "Время начала записи: не подтверждено.\n"
        f"Dropbox server_modified: {note.dropbox_modified_at.isoformat()}.\n"
        f"Получено Gate: {note.received_at.isoformat()}.\n"
        f"Расшифровано: {transcribed}.\n"
        "Назначение: private markmalina/mark, Inbox.\n"
        "Статус: входящая запись; исполнение задачи отдельно от получения.\n\n"
        f"Расшифровка, часть 1/{len(parts)}:\n\n{parts[0]}"
    )


async def store_entry(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    token: str,
    entry: dict[str, Any],
) -> None:
    file_id, revision = entry["id"], entry["rev"]
    scope = (settings.gate_workspace_id, settings.dropbox_call_account_id)
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, repr((*scope, file_id, revision))))
    async with factory() as session:
        if await session.get(WatchVoiceNote, event_id) is not None:
            return
    modified = timestamp(entry["server_modified"])
    data, _ = await _download_revision(
        http=http,
        access_token=token,
        revision=revision,
        expected_size=entry["size"],
        fallback_name=entry["name"],
    )
    duration = await _probe_duration_seconds(data)
    now = datetime.now(UTC)
    row = WatchVoiceNote(
        id=event_id,
        workspace_key=scope[0],
        account_id=scope[1],
        file_id=file_id,
        revision=revision,
        source_path=entry["path_display"],
        filename=entry["name"],
        media_type=_audio_media_type(entry["name"]),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        duration_seconds=duration,
        recorded_at=None,
        dropbox_modified_at=modified,
        received_at=now,
        transcription_state="received",
        delivery_state="pending",
        next_part=0,
    )
    if duration is None or duration > MAX_DURATION:
        row.transcription_state = "rejected"
        row.last_error = "watch_voice_media_unreadable_or_duration_exceeded"
    async with factory() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            # Only the exact already committed immutable source is an accepted race.
            old = await session.get(WatchVoiceNote, event_id)
            if old is None or old.sha256 != row.sha256 or old.size_bytes != row.size_bytes:
                raise WatchVoiceError("watch_voice_source_conflict") from None


class StoredVoice:
    def __init__(self, note: WatchVoiceNote) -> None:
        self.note = note

    async def download(self, _voice: TelegramVoiceReference) -> DownloadedVoice:
        if (
            len(self.note.data) != self.note.size_bytes
            or hashlib.sha256(self.note.data).hexdigest() != self.note.sha256
        ):
            raise WatchVoiceError("watch_voice_archive_integrity_failed")
        return DownloadedVoice(
            data=self.note.data,
            filename=self.note.filename,
            media_type=self.note.media_type,
            duration_seconds=self.note.duration_seconds,
        )


async def transcribe_note(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    note_id: str,
) -> None:
    async with factory() as session:
        claimed = await session.execute(
            update(WatchVoiceNote)
            .where(
                WatchVoiceNote.id == note_id,
                *note_scope(settings),
                WatchVoiceNote.transcription_state == "received",
            )
            .values(transcription_state="processing", attempted_at=datetime.now(UTC))
        )
        await session.commit()
        if not claimed.rowcount:  # type: ignore[attr-defined]
            return
        note = await session.get(WatchVoiceNote, note_id)
        assert note is not None
    await transcribe_claimed_note(settings, factory, http, note)


class RecoveryVoiceNormalizer(TelegramVoiceNormalizer):
    async def normalize(self, voice: DownloadedVoice) -> NormalizedVoice:
        normalized = await super().normalize(voice)
        if normalized.additional_parts:
            raise TelegramVoiceError("watch_voice_recovery_single_part_required")
        return normalized


class WatchVoiceProcessingBudget(SqlBillingBudgetService):
    """Use the actual watch processing period without changing shared billing."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, recovery_period: str | None
    ) -> None:
        super().__init__(sessions)
        self.recovery_period = recovery_period

    async def reserve(self, command: ReserveBudgetCommand, *, period_key: str) -> BudgetAdmission:
        # Normalization can cross midnight/month end after the source command
        # was constructed. Neither an archived timestamp nor that older command
        # can select the entitlement/budget period of a new provider attempt.
        now = datetime.now(UTC)
        period = now.strftime("%Y-%m")
        if self.recovery_period is not None and period != self.recovery_period:
            return BudgetAdmission(allowed=False, reason_code="watch_voice_recovery_period_changed")
        admission = await super().reserve(replace(command, occurred_at=now), period_key=period)
        finished = datetime.now(UTC)
        if admission.allowed and finished.strftime("%Y-%m") != period:
            # A database lock may also span month end. This is our new, known
            # zero-provider reservation, never a historical or unknown claim.
            if admission.reservation is not None:
                await super().release(admission.reservation, occurred_at=finished)
            return BudgetAdmission(
                allowed=False, reason_code="watch_voice_processing_period_changed"
            )
        return admission

    async def settle(
        self,
        reservation: BudgetReservation,
        *,
        actual_microusd: int,
        quantity: int,
        unit: UsageUnit,
        provider_operation_ref: str | None,
        occurred_at: datetime,
    ) -> None:
        await super().settle(
            reservation,
            actual_microusd=actual_microusd,
            quantity=quantity,
            unit=unit,
            provider_operation_ref=provider_operation_ref,
            occurred_at=datetime.now(UTC),
        )

    async def release(self, reservation: BudgetReservation, *, occurred_at: datetime) -> None:
        await super().release(reservation, occurred_at=datetime.now(UTC))

    async def mark_unknown(
        self, reservation: BudgetReservation, *, occurred_at: datetime, error_code: str
    ) -> None:
        await super().mark_unknown(
            reservation, occurred_at=datetime.now(UTC), error_code=error_code
        )


def delivery_order() -> tuple[Any, ...]:
    """An unresolved publication must not block known actionable notes."""
    return (
        case((WatchVoiceNote.delivery_state == "unknown", 1), else_=0),
        WatchVoiceNote.received_at,
        WatchVoiceNote.id,
    )


async def transcribe_claimed_note(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    note: WatchVoiceNote,
    *,
    recovery: bool = False,
    post_payment: bool = False,
) -> None:
    """Only callers with a committed normal/recovery claim may enter this boundary."""
    assert note.duration_seconds is not None
    if post_payment and not recovery:
        raise ValueError("watch_voice_post_payment_recovery_required")
    model = "gpt-4o-transcribe" if recovery else settings.gate_telegram_voice_transcription_model
    processing_at = datetime.now(UTC)
    service = TelegramVoiceTranscriptionService(
        downloader=StoredVoice(note),
        normalizer=RecoveryVoiceNormalizer() if recovery else TelegramVoiceNormalizer(),
        budget=WatchVoiceProcessingBudget(
            factory,
            recovery_period=note.received_at.strftime("%Y-%m") if recovery else None,
        ),
        speech_to_text=OpenAITranscriptionClient(
            config=OpenAITranscriptionConfig(
                api_key=settings.openai_api_key.get_secret_value(),
                enabled=True,
                model=model,
                base_url=settings.openai_api_base_url,
            ),
            http=http,
        ),
    )
    try:
        result = await service.transcribe(
            TranscribeTelegramVoiceCommand(
                voice=TelegramVoiceReference(
                    file_id=note.file_id,
                    file_unique_id=note.revision,
                    declared_size_bytes=note.size_bytes,
                    duration_seconds=note.duration_seconds,
                ),
                workspace_key=settings.gate_workspace_id,
                connector_ref="telegram_business",
                reservation_key=f"watch-voice:{note.id}"
                + (":postpayment1" if post_payment else ":recovery1" if recovery else ""),
                period_key=processing_at.strftime("%Y-%m"),
                requested_microusd=max(
                    100,
                    note.duration_seconds
                    * settings.gate_telegram_voice_reserve_microusd_per_second,
                ),
                occurred_at=processing_at,
                language="ru",
                enabled=True,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        # A provider might have accepted work. Never automatically call it a second time.
        async with factory() as session:
            await session.execute(
                update(WatchVoiceNote)
                .where(WatchVoiceNote.id == note.id)
                .values(
                    transcription_state="unknown", last_error="watch_voice_transcription_unknown"
                )
            )
            await session.commit()
        return
    async with factory() as session:
        await session.execute(
            update(WatchVoiceNote)
            .where(WatchVoiceNote.id == note.id)
            .values(
                transcription_state=result.outcome.value,
                transcript=result.alpha_input_text,
                model=model,
                transcribed_at=datetime.now(UTC),
                last_error=result.reason_code,
            )
        )
        await session.commit()


async def publish_note(
    factory: async_sessionmaker[AsyncSession],
    client: MarkInboxClient,
    note: WatchVoiceNote,
    *,
    max_parts: int | None = None,
) -> None:
    if note.transcription_state != TelegramVoiceOutcome.TRANSCRIBED.value or not note.transcript:
        return
    completed_parts = 0
    while note.delivery_state in {"pending", "unknown"}:
        if max_parts is not None and completed_parts >= max_parts:
            return
        part, body = note.next_part, issue_body(note, note.next_part)
        if note.delivery_state == "unknown":
            # No absence-of-search-result is allowed to authorize a second POST.
            receipt = await client.reconcile(part=part, issue_number=note.issue_number, body=body)
            if receipt is None:
                return
        else:
            async with factory() as session:
                claimed = await session.execute(
                    update(WatchVoiceNote)
                    .where(
                        WatchVoiceNote.id == note.id,
                        WatchVoiceNote.delivery_state == "pending",
                        WatchVoiceNote.next_part == part,
                    )
                    .values(delivery_state="sending", attempted_at=datetime.now(UTC))
                )
                await session.commit()
                if not claimed.rowcount:  # type: ignore[attr-defined]
                    return
            try:
                receipt = await client.post(
                    part=part,
                    issue_number=note.issue_number,
                    title=(
                        f"[Gate][Voice] Входящая запись {note.received_at.date()} · {note.id[:8]}"
                    ),
                    body=body,
                )
            except WatchVoiceError as exc:
                async with factory() as session:
                    await session.execute(
                        update(WatchVoiceNote)
                        .where(
                            WatchVoiceNote.id == note.id,
                            WatchVoiceNote.delivery_state == "sending",
                            WatchVoiceNote.next_part == part,
                        )
                        .values(
                            delivery_state="unknown" if exc.unknown else "blocked",
                            last_error=exc.code,
                        )
                    )
                    await session.commit()
                return
        await save_receipt(factory, note, receipt)
        completed_parts += 1


async def save_receipt(
    factory: async_sessionmaker[AsyncSession], note: WatchVoiceNote, receipt: GithubReceipt
) -> None:
    next_part = note.next_part + 1
    finished = next_part >= len(transcript_parts(note.transcript or ""))
    async with factory() as session:
        await session.execute(
            update(WatchVoiceNote)
            .where(
                WatchVoiceNote.id == note.id,
                WatchVoiceNote.next_part == note.next_part,
                WatchVoiceNote.delivery_state.in_(("sending", "unknown")),
            )
            .values(
                issue_number=receipt.number,
                issue_url=receipt.url,
                github_received_at=note.github_received_at or receipt.received_at,
                next_part=next_part,
                delivery_state="delivered" if finished else "pending",
                delivered_at=datetime.now(UTC) if finished else None,
                last_error=None,
            )
        )
        await session.commit()
    note.next_part, note.issue_number, note.issue_url = next_part, receipt.number, receipt.url
    note.github_received_at = note.github_received_at or receipt.received_at
    note.delivery_state = "delivered" if finished else "pending"


async def ensure_cursor(settings: Settings, factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        key = (settings.gate_workspace_id, settings.dropbox_call_account_id)
        if await session.get(WatchVoiceCursor, key) is None:
            session.add(WatchVoiceCursor(workspace_key=key[0], account_id=key[1]))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if await session.get(WatchVoiceCursor, key) is None:
                    raise


@observed_cycle
async def process_watch_voice(
    settings: Settings, factory: async_sessionmaker[AsyncSession], http: httpx.AsyncClient
) -> None:
    if not settings.gate_watch_voice_enabled:
        return
    if settings.gate_watch_voice_actions_enabled:
        raise WatchVoiceError("watch_voice_driver_conflict")
    validate_settings(settings)
    client = MarkInboxClient(
        http, settings.gate_mark_github_token.get_secret_value(), settings.gate_mark_github_writer
    )
    if settings.gate_watch_voice_file_id:
        # Only an already authorized exact source may bootstrap its cursor.
        await client.preflight()
        await ensure_cursor(settings, factory)
    # PostgreSQL row lock serializes the source across all web processes.
    # Originals and per-effect claims commit in separate short transactions.
    async with factory() as coordinator:
        state = await coordinator.scalar(
            select(WatchVoiceCursor)
            .where(
                WatchVoiceCursor.workspace_key == settings.gate_workspace_id,
                WatchVoiceCursor.account_id == settings.dropbox_call_account_id,
            )
            .with_for_update(skip_locked=True)
        )
        cutover = None if settings.gate_watch_voice_file_id else future_cutover(state)
        if state is None:
            return
        if cutover is not None:
            await client.preflight()
        token = await _refresh_access_token(settings, http)
        await _verify_account_id(settings, http, token)
        cursor = state.cursor
        while True:
            endpoint = "list_folder/continue" if cursor else "list_folder"
            body: dict[str, Any] = (
                {"cursor": cursor}
                if cursor
                else {"path": PATH, "recursive": False, "include_deleted": False}
            )
            try:
                response = await http.post(
                    "https://api.dropboxapi.com/2/files/" + endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
                if response.status_code != 200:
                    raise WatchVoiceError("watch_voice_delta_rejected")
                delta = response.json()
            except (httpx.HTTPError, ValueError):
                raise WatchVoiceError("watch_voice_delta_unavailable") from None
            if (
                not isinstance(delta, dict)
                or not isinstance(delta.get("entries"), list)
                or not isinstance(delta.get("cursor"), str)
                or not delta["cursor"]
                or type(delta.get("has_more")) is not bool
            ):
                raise WatchVoiceError("watch_voice_delta_invalid")
            for item in delta["entries"]:
                entry = source_entry(item)
                if (
                    entry is not None
                    and settings.gate_watch_voice_file_id
                    and (
                        entry["id"] != settings.gate_watch_voice_file_id
                        or entry["rev"] != settings.gate_watch_voice_revision
                    )
                ):
                    continue
                if entry is not None:
                    if cutover is not None and not future_entry(entry, cutover):
                        continue
                    await store_entry(settings, factory, http, token, entry)
            cursor = delta["cursor"]
            state.cursor = cursor
            if not delta["has_more"]:
                break
        OBSERVATION.scanned_at = datetime.now(UTC)
        scope = note_scope(settings)
        if cutover is not None:
            scope += future_note_scope(cutover)
        # Crashed effect claims are quarantined, never blindly retried.
        stale = datetime.now(UTC) - timedelta(hours=1)
        async with factory() as session:
            await session.execute(
                update(WatchVoiceNote)
                .where(
                    *scope,
                    WatchVoiceNote.attempted_at < stale,
                    WatchVoiceNote.transcription_state == "processing",
                )
                .values(transcription_state="unknown", last_error="watch_voice_stale_transcription")
            )
            await session.execute(
                update(WatchVoiceNote)
                .where(
                    *scope,
                    WatchVoiceNote.attempted_at < stale,
                    WatchVoiceNote.delivery_state == "sending",
                )
                .values(delivery_state="unknown", last_error="watch_voice_stale_publication")
            )
            await session.commit()
            ids = list(
                (
                    await session.scalars(
                        select(WatchVoiceNote.id)
                        .where(*scope, WatchVoiceNote.transcription_state == "received")
                        .order_by(WatchVoiceNote.received_at, WatchVoiceNote.id)
                        .limit(10)
                    )
                ).all()
            )
        for note_id in ids:
            await transcribe_note(settings, factory, http, note_id)
        async with factory() as session:
            notes = list(
                (
                    await session.scalars(
                        select(WatchVoiceNote)
                        .where(
                            *scope,
                            WatchVoiceNote.transcription_state == "transcribed",
                            WatchVoiceNote.delivery_state.in_(("pending", "unknown")),
                        )
                        .order_by(*delivery_order())
                        .limit(10)
                    )
                ).all()
            )
        for note in notes:
            await publish_note(factory, client, note)
        await coordinator.commit()


async def run_watch_voice_worker_loop(
    *, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    if not settings.gate_watch_voice_enabled:
        return
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
        while True:
            try:
                await process_watch_voice(settings, session_factory, http)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never log HTTP exception URLs, credentials, filenames or audio/text.
                logger.warning("Watch voice cycle deferred")
            await asyncio.sleep(settings.gate_watch_voice_poll_seconds)


async def operational_status(
    session: AsyncSession, settings: Settings, *, scope_allowed: bool
) -> dict[str, Any]:
    configured = False
    try:
        validate_settings(settings)
        configured = True
    except WatchVoiceError:
        pass
    counts: dict[str, int] = {}
    transcription_counts: dict[str, int] = {}
    if scope_allowed and settings.gate_watch_voice_enabled and configured:
        rows = await session.execute(
            select(WatchVoiceNote.delivery_state, func.count())
            .where(
                WatchVoiceNote.workspace_key == settings.gate_workspace_id,
                WatchVoiceNote.account_id == settings.dropbox_call_account_id,
            )
            .group_by(WatchVoiceNote.delivery_state)
        )
        counts = {str(state): count for state, count in rows}
        rows = await session.execute(
            select(WatchVoiceNote.transcription_state, func.count())
            .where(
                WatchVoiceNote.workspace_key == settings.gate_workspace_id,
                WatchVoiceNote.account_id == settings.dropbox_call_account_id,
            )
            .group_by(WatchVoiceNote.transcription_state)
        )
        transcription_counts = {str(state): count for state, count in rows}
    return {
        "enabled": settings.gate_watch_voice_enabled,
        "configured": configured,
        "scope_allowed": scope_allowed,
        "delivery_counts": counts,
        "transcription_counts": transcription_counts,
        "private_content_exposed": False,
    }


async def resume_rejected_delivery(
    settings: Settings, factory: async_sessionmaker[AsyncSession], note_id: str
) -> bool:
    """Operator recovery after fixing a definite rejection; never retries unknown effects."""
    validate_settings(settings)
    note_id = str(uuid.UUID(note_id))
    async with factory() as session:
        result = await session.execute(
            update(WatchVoiceNote)
            .where(
                WatchVoiceNote.id == note_id,
                *note_scope(settings),
                WatchVoiceNote.transcription_state == "transcribed",
                WatchVoiceNote.delivery_state == "blocked",
                WatchVoiceNote.last_error == "github_post_rejected",
            )
            .values(delivery_state="pending", last_error=None)
        )
        await session.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]

