"""Consume existing grants to recover stored Telegram voice transcripts."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.audio_summary import AUDIO_SUMMARY_MODEL, build_audio_summary
from app.application.telegram_voice_transcription import (
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
from app.infrastructure.owner_voice_recovery import (
    claim_owner_voice_recovery,
    owner_voice_reservation_key,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
    TelegramVoiceArchive,
)
from app.worker.stored_audio import (
    STORED_VOICE_BUDGET_CONNECTOR_REF,
    STORED_VOICE_BUDGET_SCOPE_KIND,
    StoredVoiceDownloader,
    store_no_speech_summary,
)

_TELEGRAM_VOICE_RECOVERY_GENERATION = 2


class TelegramVoiceRecoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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
            raise TelegramVoiceRecoveryError(
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
            raise TelegramVoiceRecoveryError(
                "telegram_voice_recovery_archive_invalid"
            )

        # Commit the generation consumption before provider I/O. A crash now
        # fails closed: a new owner authorization is required for another try.
        archive.transcription_recovery_generation = None
        archive.reset_processing_attempt(queued_at=None)
        await session.commit()
        await session.refresh(archive)
        return archive, generation


def _telegram_voice_recovery_reservation_key(
    archive_id: object,
    generation: int,
) -> str:
    """Bind one recovery reservation to one isolated authorization generation."""

    if generation != _TELEGRAM_VOICE_RECOVERY_GENERATION:
        raise TelegramVoiceRecoveryError(
            "telegram_voice_recovery_generation_invalid"
        )
    return f"telegram-voice-recovery-v{generation}:{archive_id}"


async def recover_authorized_telegram_voice(
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

    async def mark_recovery_started(at: datetime) -> None:
        async with session_factory.begin() as session:
            started = await session.scalar(
                update(TelegramVoiceArchive)
                .where(
                    TelegramVoiceArchive.id == archive.id,
                    TelegramVoiceArchive.recognition_started_at.is_(None),
                    TelegramVoiceArchive.transcription_status == archive.transcription_status,
                    TelegramVoiceArchive.transcription_recovery_generation.is_(None),
                )
                .values(recognition_started_at=at)
                .returning(TelegramVoiceArchive.id)
            )
            if started is None:
                raise TelegramVoiceRecoveryError("telegram_voice_recovery_recognition_claim_lost")

    service = TelegramVoiceTranscriptionService(
        downloader=StoredVoiceDownloader(archive),
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
            connector_ref=STORED_VOICE_BUDGET_CONNECTOR_REF,
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
            budget_scope_kind=STORED_VOICE_BUDGET_SCOPE_KIND,
            on_recognition_start=mark_recovery_started,
        )
    )
    async with session_factory() as session:
        stored = await session.get(
            TelegramVoiceArchive,
            archive.id,
            with_for_update=True,
        )
        if stored is None:
            raise TelegramVoiceRecoveryError(
                "telegram_voice_recovery_archive_missing"
            )
        stored.transcription_status = result.outcome.value
        stored.transcript_text = result.alpha_input_text
        stored.transcript_language = "ru"
        stored.transcription_model = settings.gate_telegram_voice_transcription_model
        stored.transcription_reason_code = result.reason_code
        stored.transcribed_at = datetime.now(UTC)
        stored.record_processing_result(
            stored.transcribed_at,
            reason=None if result.alpha_input_text is not None else result.reason_code,
        )
        if result.alpha_input_text:
            stored.summary_status = "summarized"
            stored.summary_text = build_audio_summary(result.alpha_input_text)
            stored.summary_model = AUDIO_SUMMARY_MODEL
            stored.summary_reason_code = "audio_summary_generated"
            stored.summarized_at = now
        elif result.reason_code == "telegram_voice_no_speech":
            store_no_speech_summary(stored)
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
