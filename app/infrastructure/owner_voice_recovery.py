"""One owner-authorized archive-only recovery; never requeue message delivery."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.billing import BillingBudgetReservationModel
from app.models.canonical import Workspace
from app.models.domain import TelegramVoiceArchive

GENERATION = 5
ACCOUNT_ID = UUID("0d8e025f-323d-5e86-a44c-a620f690ae4f")
PROJECT_ID = UUID("e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0")
EXACT_ARCHIVES = (UUID("a4901376-2dee-49b3-bc7e-011716842c2f"),)

REASONS = (
    "openai_transcription_transport_unknown",
    "openai_transcription_response_unknown",
    "billing_reservation_replayed",
    "billing_settlement_exceeds_reservation",
)


async def claim_owner_voice_recovery(
    session_factory: async_sessionmaker[AsyncSession], *, workspace_key: str,
) -> TelegramVoiceArchive | None:
    """Consume the persistent grant before I/O, including on uncertain failures."""
    if workspace_key != "mark-personal":
        return None
    async with session_factory() as session:
        archive = await session.scalar(
            select(TelegramVoiceArchive).where(
                TelegramVoiceArchive.id.in_(EXACT_ARCHIVES),
                TelegramVoiceArchive.account_id == ACCOUNT_ID,
                TelegramVoiceArchive.project_id == PROJECT_ID,
                TelegramVoiceArchive.transcription_recovery_generation == GENERATION,
                TelegramVoiceArchive.transcription_status.in_(("unknown", "rejected")),
                TelegramVoiceArchive.transcription_reason_code.in_(REASONS),
                TelegramVoiceArchive.transcript_text.is_(None),
                TelegramVoiceArchive.summary_text.is_(None),
            ).order_by(TelegramVoiceArchive.received_at).limit(1)
            .with_for_update(skip_locked=True)
        )
        if archive is None:
            return None
        if (
            not archive.data or len(archive.data) != archive.size_bytes
            or archive.duration_seconds <= 0
            or hashlib.sha256(archive.data).hexdigest() != archive.sha256
        ):
            raise ValueError("owner_voice_recovery_archive_invalid")
        archive.transcription_recovery_generation = None
        archive.transcription_recovery_authorized_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(archive)
        return archive


def owner_voice_reservation_key(archive_id: UUID) -> str:
    if archive_id not in EXACT_ARCHIVES:
        raise ValueError("owner_voice_recovery_scope_invalid")
    return f"telegram-voice-recovery-v5-20260921:{archive_id}"


async def reconcile_exact_call_copies(
    session_factory: async_sessionmaker[AsyncSession], *, workspace_key: str,
) -> None:
    """Restore only four historical copies after byte-for-byte source equality."""
    if workspace_key != "mark-personal":
        return
    async with session_factory() as session:
        await session.execute(text("""
            WITH matches AS (
                SELECT DISTINCT ON (target.id) target.id, source.id AS source_id
                FROM phone_call_recordings AS target
                JOIN phone_call_recordings AS source
                  ON source.workspace_key = target.workspace_key
                 AND source.project_id = target.project_id
                 AND source.source_account_id = target.source_account_id
                 AND source.sha256 = target.sha256
                 AND source.size_bytes = target.size_bytes
                 AND source.data = target.data
                WHERE target.workspace_key = 'mark-personal'
                  AND target.id IN (
                    '0db01180-ba79-4ba5-b253-dd2eaf86b9ab'::uuid,
                    'b9da7c16-543a-417a-9ec8-a844b43c9efc'::uuid,
                    '8209f319-fd08-4ce4-898a-b21f3ab9522d'::uuid,
                    '66d81bfc-7884-4c1f-80ae-5d14adce2122'::uuid)
                  AND target.transcription_status IN ('rejected', 'unknown')
                  AND target.transcript_text IS NULL
                  AND ((source.transcription_status = 'transcribed'
                        AND source.transcript_text IS NOT NULL)
                       OR (source.transcription_status = 'rejected'
                           AND source.transcription_reason_code = 'telegram_voice_no_speech'))
                  AND target.transcription_reason_code IS DISTINCT FROM 'telegram_voice_no_speech'
                  AND source.summary_status = 'summarized'
                ORDER BY target.id, source.transcribed_at DESC, source.id
            )
            UPDATE phone_call_recordings AS target
            SET transcription_status = source.transcription_status,
                transcript_text = source.transcript_text,
                transcript_language = source.transcript_language,
                transcription_model = source.transcription_model,
                transcription_reason_code = CASE
                    WHEN source.transcription_reason_code = 'telegram_voice_no_speech'
                    THEN 'telegram_voice_no_speech' ELSE 'audio_identical_source_reused' END,
                transcribed_at = source.transcribed_at,
                summary_status = source.summary_status, summary_text = source.summary_text,
                summary_model = source.summary_model,
                summary_reason_code = source.summary_reason_code,
                summarized_at = source.summarized_at
            FROM matches, phone_call_recordings AS source
            WHERE target.id = matches.id AND source.id = matches.source_id
              AND target.transcript_text IS NULL
        """))
        await session.commit()


async def owner_voice_archive_inventory(
    session: AsyncSession, *, workspace_key: str,
    account_ids: frozenset[UUID], project_ids: frozenset[UUID],
) -> dict[str, Any]:
    """Count the entire authorized archive without reading private audio or text."""
    result: dict[str, Any] = {
        "total": 0, "completed": 0, "incomplete": 0,
        "incomplete_recordings": [], "truncated": False,
        "private_content_exposed": False,
        "no_speech_count": 0, "requires_attention_count": 0,
        "historical_short_voice_overflow_count": 0,
    }
    if workspace_key != "mark-personal" or not account_ids or not project_ids:
        return result
    if ACCOUNT_ID in account_ids and PROJECT_ID in project_ids:
        result["historical_short_voice_overflow_count"] = int((await session.scalar(
            select(func.count()).select_from(BillingBudgetReservationModel)
            .join(Workspace, Workspace.id == BillingBudgetReservationModel.workspace_id)
            .where(
                Workspace.key == workspace_key,
                BillingBudgetReservationModel.reservation_key == (
                    "telegram-voice-recovery-v4-20260921:"
                    "a4901376-2dee-49b3-bc7e-011716842c2f"
                ),
                BillingBudgetReservationModel.state == "unknown",
                BillingBudgetReservationModel.error_code
                == "billing_settlement_exceeds_reservation",
            )
        )) or 0)
    archive = TelegramVoiceArchive
    scope = (archive.account_id.in_(account_ids), archive.project_id.in_(project_ids))
    complete = and_(archive.transcription_status == "transcribed",
                    archive.transcript_text.is_not(None))
    no_speech = and_(archive.transcription_status == "rejected",
                    archive.transcription_reason_code == "telegram_voice_no_speech")
    counts = (await session.execute(select(
        func.count(), func.count().filter(complete), func.count().filter(no_speech),
    ).select_from(archive).where(*scope))).one()
    missing = int(counts[0]) - int(counts[1])
    result.update(total=int(counts[0]), completed=int(counts[1]),
                  incomplete=missing, truncated=missing > 100)
    result.update(no_speech_count=int(counts[2]), requires_attention_count=missing - int(counts[2]))
    rows = (await session.execute(select(
        archive.id, archive.received_at, archive.transcription_status,
        archive.transcription_reason_code, archive.duration_seconds,
        archive.size_bytes, archive.summary_status,
    ).where(*scope, or_(archive.transcription_status.is_(None),
                       archive.transcription_status != "transcribed",
                       archive.transcript_text.is_(None)))
        .order_by(archive.received_at).limit(100))).all()
    result["incomplete_recordings"] = [{
        "archive_id": str(row.id), "received_at": row.received_at.isoformat(),
        "status": row.transcription_status, "reason_code": row.transcription_reason_code,
        "duration_seconds": row.duration_seconds, "size_bytes": row.size_bytes,
        "summary_status": row.summary_status,
    } for row in rows]
    return result
