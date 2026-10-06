"""Explicit private owner-authorized quota probe for one existing definitely rejected note."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.models.billing import BillingBudgetReservationModel, BillingEntitlementModel
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceNote, WatchVoiceRecoveryClaim
from app.worker import watch_voice as worker

RECOVERY_ERRORS = {
    "openai_transcription_rate_limited",  # legacy429; actual quota proven separately
    "openai_transcription_quota_unavailable",
}


def authorized_at(value: object) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z", value
        )
        is None
    ):
        raise WatchVoiceError("watch_voice_recovery_approval_invalid")
    try:
        timestamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise WatchVoiceError("watch_voice_recovery_approval_invalid") from None
    if timestamp > datetime.now(UTC) + timedelta(seconds=30):
        raise WatchVoiceError("watch_voice_recovery_approval_invalid")
    return timestamp


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def recover_note(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    binding: dict[str, Any],
    *,
    authorization_sha: str,
) -> WatchVoiceNote:
    """After current private Actions preflight; this never imports or rearms a note."""
    approval_at = authorized_at(binding["authorized_at"])
    now = datetime.now(UTC)
    try:
        async with factory() as session, session.begin():
            note: WatchVoiceNote | None = await session.scalar(
                select(WatchVoiceNote)
                .where(WatchVoiceNote.id == binding["event_id"], *worker.note_scope(settings))
                .with_for_update()
            )
            if (
                note is None
                or note.sha256 != binding["expected_audio_sha256"]
                or not 0 < note.size_bytes <= worker.SOURCE_LIMIT
                or len(note.data) != note.size_bytes
                or hashlib.sha256(note.data).hexdigest() != note.sha256
                or not note.source_path.casefold().startswith(worker.PATH.casefold() + "/")
                or note.duration_seconds is None
                or not 0 < note.duration_seconds <= worker.MAX_DURATION
            ):
                raise WatchVoiceError("watch_voice_recovery_source_mismatch")
            existing = await session.get(WatchVoiceRecoveryClaim, note.id)
            if existing is not None:
                if note.model != "gpt-4o-transcribe":
                    raise WatchVoiceError("watch_voice_recovery_receipt_mismatch")
                # A changed private config/main/run never extends the per-note cap.
                if existing.outcome == "claimed" and note.transcription_state in {
                    "transcribed",
                    "rejected",
                    "unknown",
                }:
                    existing.outcome = note.transcription_state
                    existing.last_error = note.last_error
                    existing.finished_at = now
                elif existing.outcome != "claimed" and existing.outcome != note.transcription_state:
                    raise WatchVoiceError("watch_voice_recovery_receipt_mismatch")
                if (
                    existing.outcome == "claimed"
                    and _utc(existing.claimed_at) < now - timedelta(hours=1)
                    and note.transcription_state == "processing"
                ):
                    note.transcription_state = "unknown"
                    note.last_error = "watch_voice_stale_transcription"
                    existing.outcome = "unknown"
                    existing.last_error = note.last_error
                    existing.finished_at = now
                if (
                    note.transcription_state == "transcribed"
                    and note.delivery_state == "sending"
                    and note.attempted_at is not None
                    and _utc(note.attempted_at) < now - timedelta(hours=1)
                ):
                    # A crash after POST may leave an acknowledged remote issue
                    # without its local receipt. Quarantine only the observed
                    # exact publication claim; never rearm it for another POST.
                    await session.execute(
                        update(WatchVoiceNote)
                        .where(
                            WatchVoiceNote.id == note.id,
                            *worker.note_scope(settings),
                            WatchVoiceNote.sha256 == binding["expected_audio_sha256"],
                            WatchVoiceNote.transcription_state == "transcribed",
                            WatchVoiceNote.delivery_state == "sending",
                            WatchVoiceNote.next_part == note.next_part,
                            WatchVoiceNote.attempted_at == note.attempted_at,
                            WatchVoiceNote.attempted_at < now - timedelta(hours=1),
                        )
                        .values(
                            delivery_state="unknown",
                            last_error="watch_voice_stale_publication",
                        )
                        .execution_options(synchronize_session=False)
                    )
                    await session.refresh(note)
                return note
            if (
                note.transcription_state != "rejected"
                or note.last_error != binding["expected_error"]
                or note.last_error not in RECOVERY_ERRORS
                or note.model != "gpt-4o-mini-transcribe"
                or note.transcript is not None
                or note.delivery_state != "pending"
                or note.next_part != 0
                or note.issue_number is not None
                or note.issue_url is not None
                or note.github_received_at is not None
                or note.delivered_at is not None
                or note.attempted_at is None
                or approval_at <= _utc(note.attempted_at)
                or _utc(note.received_at).strftime("%Y-%m") != now.strftime("%Y-%m")
            ):
                raise WatchVoiceError("watch_voice_recovery_rejected_attempt_required")
            workspace = await session.scalar(
                select(Workspace).where(
                    Workspace.key == settings.gate_workspace_id, Workspace.is_active.is_(True)
                )
            )
            if workspace is None:
                raise WatchVoiceError("watch_voice_recovery_workspace_invalid")
            reservation = await session.scalar(
                select(BillingBudgetReservationModel).where(
                    BillingBudgetReservationModel.workspace_id == workspace.id,
                    BillingBudgetReservationModel.reservation_key == f"watch-voice:{note.id}",
                    BillingBudgetReservationModel.product == "gate",
                    BillingBudgetReservationModel.operation == "transcription",
                    BillingBudgetReservationModel.scope_kind == "telegram_connector",
                    BillingBudgetReservationModel.scope_ref == "telegram_business",
                    BillingBudgetReservationModel.period_key
                    == _utc(note.received_at).strftime("%Y-%m"),
                    BillingBudgetReservationModel.state == "released",
                )
            )
            entitlements = (
                await session.scalars(
                    select(BillingEntitlementModel).where(
                        BillingEntitlementModel.workspace_id == workspace.id,
                        BillingEntitlementModel.product == "gate",
                        BillingEntitlementModel.scope_kind == "telegram_connector",
                        BillingEntitlementModel.scope_ref == "telegram_business",
                        BillingEntitlementModel.state == "active",
                        BillingEntitlementModel.valid_from <= note.received_at,
                        (BillingEntitlementModel.valid_until.is_(None))
                        | (BillingEntitlementModel.valid_until > now),
                    )
                )
            ).all()
            if (
                reservation is None
                or len(entitlements) != 1
                or entitlements[0].hard_limit_microusd != 3_000_000
            ):
                raise WatchVoiceError("watch_voice_recovery_released_reservation_required")
            session.add(
                WatchVoiceRecoveryClaim(
                    note_id=note.id,
                    original_reservation_id=reservation.id,
                    original_error=note.last_error,
                    original_model=note.model,
                    original_attempted_at=note.attempted_at,
                    authorization_sha=authorization_sha,
                    authorization_kind="owner_quota_probe",
                    authorized_at=approval_at,
                    claimed_at=now,
                    outcome="claimed",
                    finished_at=None,
                    last_error=None,
                )
            )
            note.transcription_state = "processing"
            note.model = "gpt-4o-transcribe"
            note.attempted_at = now
            await session.flush()
    except IntegrityError:
        # SQLite also proves the unique journal cap; PostgreSQL serializes the row lock.
        async with factory() as session:
            winner = await session.get(WatchVoiceRecoveryClaim, binding["event_id"])
            note = await session.scalar(
                select(WatchVoiceNote).where(
                    WatchVoiceNote.id == binding["event_id"], *worker.note_scope(settings)
                )
            )
        if (
            winner is None
            or note is None
            or note.sha256 != binding["expected_audio_sha256"]
            or note.model != "gpt-4o-transcribe"
        ):
            raise WatchVoiceError("watch_voice_recovery_claim_unavailable") from None
        return note
    await worker.transcribe_claimed_note(settings, factory, http, note, recovery=True)
    async with factory() as session, session.begin():
        current = await session.get(WatchVoiceNote, note.id)
        claim = await session.get(WatchVoiceRecoveryClaim, note.id)
        if current is None or claim is None:
            raise WatchVoiceError("watch_voice_recovery_claim_unavailable")
        claim.outcome = current.transcription_state
        claim.last_error = current.last_error
        claim.finished_at = datetime.now(UTC)
        return current


async def protected_recovery_receipt(
    session: AsyncSession, note: WatchVoiceNote
) -> dict[str, Any] | None:
    """Only after the existing owner archive-read guard; no private source/token fields."""
    claim = await session.get(WatchVoiceRecoveryClaim, note.id)
    if claim is None:
        return None
    original = await session.scalar(
        select(BillingBudgetReservationModel)
        .join(Workspace, Workspace.id == BillingBudgetReservationModel.workspace_id)
        .where(
            Workspace.key == note.workspace_key,
            BillingBudgetReservationModel.id == claim.original_reservation_id,
        )
    )
    recovered = None
    if original is not None:
        recovered = await session.scalar(
            select(BillingBudgetReservationModel).where(
                BillingBudgetReservationModel.workspace_id == original.workspace_id,
                BillingBudgetReservationModel.reservation_key == f"watch-voice:{note.id}:recovery1",
            )
        )
    return {
        "original_error": claim.original_error,
        "original_model": claim.original_model,
        "original_attempted_at": _utc(claim.original_attempted_at).isoformat(),
        "original_reservation_released": original is not None and original.state == "released",
        "authorization_sha": claim.authorization_sha,
        "authorization_kind": claim.authorization_kind,
        "provider_readiness": provider_readiness(note),
        "authorized_at": _utc(claim.authorized_at).isoformat(),
        "claimed_at": _utc(claim.claimed_at).isoformat(),
        "outcome": claim.outcome,
        "finished_at": _utc(claim.finished_at).isoformat() if claim.finished_at else None,
        "last_error": claim.last_error,
        "reservation_state": recovered.state if recovered is not None else None,
        "actual_microusd": recovered.settled_microusd if recovered is not None else None,
    }


def provider_readiness(note: WatchVoiceNote) -> str:
    """Observation from this bounded attempt, never a global account guarantee."""
    if note.transcription_state == "transcribed":
        return "available_for_attempt"
    if note.last_error == "openai_transcription_quota_unavailable":
        return "quota_unavailable"
    if note.last_error == "openai_transcription_rate_limited":
        return "rate_limited"
    if note.transcription_state == "unknown":
        return "unknown"
    return "not_established"
