"""One explicit paid continuation of the same definitely rejected archived note."""

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
from app.infrastructure.watch_voice_recovery import (
    RECOVERY_ERRORS,
    _utc,
    attempt_readiness,
    authorized_at,
)
from app.models.billing import BillingBudgetReservationModel, BillingEntitlementModel
from app.models.canonical import Workspace
from app.models.watch_voice import (
    WatchVoiceNote,
    WatchVoicePostPaymentClaim,
    WatchVoiceRecoveryClaim,
)
from app.worker import watch_voice as worker


async def continue_note(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    binding: dict[str, Any],
    *,
    authorization_sha: str,
) -> WatchVoiceNote:
    """Called only after the unchanged private Actions preflight; never resets recovery1."""
    approval_at = authorized_at(binding["authorized_at"])
    now = datetime.now(UTC)
    if re.fullmatch(r"[a-f0-9]{40}", authorization_sha) is None:
        raise WatchVoiceError("watch_voice_post_payment_approval_invalid")
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
                raise WatchVoiceError("watch_voice_post_payment_source_mismatch")
            existing = await session.get(WatchVoicePostPaymentClaim, note.id)
            if existing is not None:
                if note.model != "gpt-4o-transcribe":
                    raise WatchVoiceError("watch_voice_post_payment_receipt_mismatch")
                if existing.outcome == "claimed" and note.transcription_state in {
                    "transcribed",
                    "rejected",
                    "unknown",
                }:
                    existing.outcome = note.transcription_state
                    existing.last_error = note.last_error
                    existing.finished_at = now
                elif existing.outcome != "claimed" and existing.outcome != note.transcription_state:
                    raise WatchVoiceError("watch_voice_post_payment_receipt_mismatch")
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
                            delivery_state="unknown", last_error="watch_voice_stale_publication"
                        )
                        .execution_options(synchronize_session=False)
                    )
                    await session.refresh(note)
                return note
            prior = await session.get(WatchVoiceRecoveryClaim, note.id)
            if (
                prior is None
                or prior.outcome != "rejected"
                or prior.last_error != "openai_transcription_quota_unavailable"
                or prior.original_model != "gpt-4o-mini-transcribe"
                or prior.original_error not in RECOVERY_ERRORS
                or prior.authorization_kind != "owner_quota_probe"
                or prior.finished_at is None
                or note.transcription_state != "rejected"
                or note.last_error != binding["expected_error"]
                or note.last_error != prior.last_error
                or note.model != "gpt-4o-transcribe"
                or note.transcript is not None
                or note.delivery_state != "pending"
                or note.next_part != 0
                or note.issue_number is not None
                or note.issue_url is not None
                or note.github_received_at is not None
                or note.delivered_at is not None
                or note.attempted_at is None
                or _utc(note.attempted_at) != _utc(prior.claimed_at)
                or approval_at <= _utc(prior.finished_at)
                or approval_at <= _utc(prior.claimed_at)
                or _utc(note.received_at).strftime("%Y-%m") != now.strftime("%Y-%m")
            ):
                raise WatchVoiceError("watch_voice_post_payment_rejected_attempt_required")
            workspace = await session.scalar(
                select(Workspace).where(
                    Workspace.key == settings.gate_workspace_id, Workspace.is_active.is_(True)
                )
            )
            if workspace is None:
                raise WatchVoiceError("watch_voice_post_payment_workspace_invalid")
            period = _utc(note.received_at).strftime("%Y-%m")
            reservations = []
            for key in (f"watch-voice:{note.id}", f"watch-voice:{note.id}:recovery1"):
                reservations.append(
                    await session.scalar(
                        select(BillingBudgetReservationModel).where(
                            BillingBudgetReservationModel.workspace_id == workspace.id,
                            BillingBudgetReservationModel.reservation_key == key,
                            BillingBudgetReservationModel.product == "gate",
                            BillingBudgetReservationModel.operation == "transcription",
                            BillingBudgetReservationModel.scope_kind == "telegram_connector",
                            BillingBudgetReservationModel.scope_ref == "telegram_business",
                            BillingBudgetReservationModel.period_key == period,
                            BillingBudgetReservationModel.state == "released",
                        )
                    )
                )
            original, previous = reservations
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
                original is None
                or previous is None
                or original.id != prior.original_reservation_id
                or len(entitlements) != 1
                or entitlements[0].hard_limit_microusd != 3_000_000
            ):
                raise WatchVoiceError("watch_voice_post_payment_released_reservations_required")
            session.add(
                WatchVoicePostPaymentClaim(
                    note_id=note.id,
                    previous_reservation_id=previous.id,
                    previous_error=note.last_error,
                    previous_model=note.model,
                    previous_attempted_at=note.attempted_at,
                    authorization_sha=authorization_sha,
                    authorization_kind="owner_post_payment",
                    authorized_at=approval_at,
                    claimed_at=now,
                    outcome="claimed",
                    finished_at=None,
                    last_error=None,
                )
            )
            note.transcription_state = "processing"
            note.attempted_at = now
            await session.flush()
    except IntegrityError:
        async with factory() as session:
            winner = await session.get(WatchVoicePostPaymentClaim, binding["event_id"])
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
            raise WatchVoiceError("watch_voice_post_payment_claim_unavailable") from None
        return note
    await worker.transcribe_claimed_note(
        settings, factory, http, note, recovery=True, post_payment=True
    )
    async with factory() as session, session.begin():
        current = await session.get(WatchVoiceNote, note.id)
        claim = await session.get(WatchVoicePostPaymentClaim, note.id)
        if current is None or claim is None:
            raise WatchVoiceError("watch_voice_post_payment_claim_unavailable")
        claim.outcome = current.transcription_state
        claim.last_error = current.last_error
        claim.finished_at = datetime.now(UTC)
        return current


async def protected_post_payment_receipt(
    session: AsyncSession, note: WatchVoiceNote
) -> dict[str, Any] | None:
    """Read only after the existing owner archive guard; no credential/payment payload."""
    claim = await session.get(WatchVoicePostPaymentClaim, note.id)
    if claim is None:
        return None
    reservation = await session.scalar(
        select(BillingBudgetReservationModel)
        .join(Workspace, Workspace.id == BillingBudgetReservationModel.workspace_id)
        .where(
            Workspace.key == note.workspace_key,
            BillingBudgetReservationModel.reservation_key == f"watch-voice:{note.id}:postpayment1",
        )
    )
    return {
        "previous_error": claim.previous_error,
        "previous_model": claim.previous_model,
        "previous_attempted_at": _utc(claim.previous_attempted_at).isoformat(),
        "authorization_sha": claim.authorization_sha,
        "authorization_kind": claim.authorization_kind,
        "authorized_at": _utc(claim.authorized_at).isoformat(),
        "claimed_at": _utc(claim.claimed_at).isoformat(),
        "outcome": claim.outcome,
        "finished_at": _utc(claim.finished_at).isoformat() if claim.finished_at else None,
        "last_error": claim.last_error,
        "provider_readiness": attempt_readiness(claim.outcome, claim.last_error),
        "reservation_state": reservation.state if reservation is not None else None,
        "actual_microusd": reservation.settled_microusd if reservation is not None else None,
    }
