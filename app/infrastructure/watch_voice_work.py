"""Default-off internal Work coordination; no STT, scheduling or public write tool.

The existing received->processing Note CAS is the shared Gate/Work dispatch
barrier. Work cannot take over a Gate attempt in this initial implementation.
Commands own short DB transactions; client tokens and identities are hashed.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from app.core.config import Settings
from app.infrastructure.watch_voice_github import (
    GithubReceipt,
    MarkInboxClient,
    WatchVoiceError,
    body_digest,
    transcript_parts,
)
from app.infrastructure.watch_voice_inventory import iso, utc
from app.models.watch_voice import (
    WatchVoiceCursor,
    WatchVoiceNote,
    WatchVoiceSource,
    WatchVoiceTextReceipt,
    WatchVoiceWorkClaim,
    WatchVoiceWorkResult,
)

Factory = async_sessionmaker[AsyncSession]
TERMINAL_TEXT = {"prepared", "saved", "accepted"}


async def claim_gate_dispatch(
    settings: Settings, factory: Factory, note_id: str,
) -> WatchVoiceNote | None:
    """Re-read Work fences under the same Note lock before Gate claims processing."""
    async with factory() as session:
        note = await session.scalar(_query(settings, note_id).where(
            WatchVoiceNote.transcription_state == "received").with_for_update())
        if note is None:
            return None
        result = await session.get(WatchVoiceWorkResult, note_id)
        if result is not None:
            _pin(note, result)
            if result.state != "idle":
                return None
        changed = await session.execute(update(WatchVoiceNote).where(
            WatchVoiceNote.id == note_id, WatchVoiceNote.transcription_state == "received",
        ).values(transcription_state="processing", attempted_at=datetime.now(UTC)))
        await session.commit()
        return note if changed.rowcount else None  # type: ignore[attr-defined]


def _enabled(settings: Settings) -> None:
    if not (settings.gate_watch_voice_work_reserve_enabled
            and settings.gate_watch_voice_early_inbox_enabled
            and settings.gate_watch_voice_data_gate_approved
            and settings.gate_watch_voice_workspace_key
            and settings.gate_watch_voice_workspace_key == settings.gate_workspace_id
            and settings.dropbox_call_account_id.strip()):
        raise WatchVoiceError("watch_voice_work_disabled")
    if bool(settings.gate_watch_voice_file_id) != bool(settings.gate_watch_voice_revision):
        raise WatchVoiceError("watch_voice_work_source_binding_invalid")


def _credentials(note_id: str, actor: str, token: str) -> tuple[str, str, str]:
    try:
        key = str(uuid.UUID(note_id))
    except (ValueError, TypeError, AttributeError):
        raise WatchVoiceError("watch_voice_work_arguments_invalid") from None
    if (not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 255
            or not isinstance(token, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) is None):
        raise WatchVoiceError("watch_voice_work_arguments_invalid")
    return key, body_digest(actor.strip()), body_digest(token)


def _query(settings: Settings, key: str) -> Select[tuple[WatchVoiceNote]]:
    query = select(WatchVoiceNote).where(
        WatchVoiceNote.id == key,
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
    )
    if settings.gate_watch_voice_file_id:
        query = query.where(WatchVoiceNote.file_id == settings.gate_watch_voice_file_id,
                            WatchVoiceNote.revision == settings.gate_watch_voice_revision)
    return query


async def _note(session: AsyncSession, settings: Settings, key: str,
                *, original: bool = False) -> WatchVoiceNote:
    query = _query(settings, key).with_for_update()
    if not original:
        query = query.options(defer(WatchVoiceNote.data))
    note = await session.scalar(query)
    if note is None:
        raise WatchVoiceError("watch_voice_work_source_not_found")
    return note


def _pin(note: WatchVoiceNote, result: WatchVoiceWorkResult) -> None:
    if (note.revision, note.sha256, note.issue_number, note.issue_url) != (
        result.revision, result.audio_sha256, result.issue_number, result.issue_url
    ):
        raise WatchVoiceError("watch_voice_work_source_conflict")


def validate_work_candidate(note: Any, result: Any, claim: Any) -> tuple[Any, ...]:
    """Validate the entire submitted candidate; expiry never revokes submitted text.

    Pure so protected status can use its one caller-owned, no-autoflush SELECT.
    The returned immutable fingerprint must also survive external readback.
    """
    def conflict() -> None:
        raise WatchVoiceError("watch_voice_work_candidate_conflict")

    if (claim is None or result.state not in TERMINAL_TEXT
            or result.note_id != note.id or result.claim_id != claim.id
            or claim.note_id != note.id or result.epoch != claim.epoch or result.epoch < 1
            or claim.state != "submitted" or note.transcription_state != "transcribed"
            or note.transcript_origin != "work" or note.model is not None
            or (note.revision, note.sha256, note.issue_number, note.issue_url)
            != (result.revision, result.audio_sha256, result.issue_number, result.issue_url)
            or not isinstance(result.transcript, str) or not result.transcript.strip()
            or note.transcript != result.transcript):
        conflict()
    try:
        encoded = result.transcript.encode("utf-8")
    except UnicodeEncodeError:
        conflict()
        raise AssertionError("unreachable") from None
    if (not 1 <= len(result.transcript) <= 100_000 or len(encoded) > 400_000
            or result.characters != len(result.transcript) or result.utf8_bytes != len(encoded)
            or result.transcript_sha256 != hashlib.sha256(encoded).hexdigest()):
        conflict()
    times = (claim.created_at, claim.started_at, claim.submitted_at, claim.expires_at,
             result.prepared_at, note.transcribed_at, note.processing_finished_at)
    if any(value is None for value in times):
        conflict()
    created, started, submitted, expires, prepared, transcribed, finished = map(utc, times)
    if not (created <= started <= submitted < expires
            and submitted == prepared == transcribed == finished):
        conflict()
    if result.state == "prepared":
        if result.saved_at is not None or result.accepted_at is not None:
            conflict()
    elif result.saved_at is None or utc(result.saved_at) < prepared:
        conflict()
    if result.state == "saved" and result.accepted_at is not None:
        conflict()
    if result.state == "accepted" and (
        result.accepted_at is None or utc(result.accepted_at) < utc(result.saved_at)
    ):
        conflict()
    return (result.note_id, result.claim_id, result.epoch, result.revision,
            result.audio_sha256, result.issue_number, result.issue_url,
            result.transcript, result.transcript_sha256, result.characters, result.utf8_bytes,
            *(iso(value) for value in times), claim.id, claim.note_id, claim.epoch, claim.state,
            note.file_id, note.size_bytes, iso(note.dropbox_modified_at), iso(note.received_at))


async def _candidate(session: AsyncSession, note: WatchVoiceNote,
                     result: WatchVoiceWorkResult) -> tuple[Any, ...]:
    claim = await session.get(WatchVoiceWorkClaim, result.claim_id) if result.claim_id else None
    return validate_work_candidate(note, result, claim)


def validate_work_ledger(note: Any, parts: list[Any]) -> list[tuple[Any, ...]]:
    """Bind logical parts to exact comment identities in strict creation order."""
    from datetime import datetime as datetime_type

    from app.worker.watch_voice import issue_body

    expected_parts = len(transcript_parts(note.transcript or ""))
    if [p.part for p in parts] != list(range(expected_parts)):
        raise WatchVoiceError("watch_voice_work_receipts_incomplete")
    previous_id = 0
    previous_time: datetime | None = None
    snapshot: list[tuple[Any, ...]] = []
    for part in parts:
        if (type(part.comment_id) is not int or not previous_id < part.comment_id <= 2**63 - 1
                or not isinstance(part.received_at, datetime_type)
                or not isinstance(part.writer, str) or not part.writer
                or (part.issue_number, part.issue_url) != (note.issue_number, note.issue_url)
                or body_digest(issue_body(note, part.part, publication_part=part.part + 1))
                != part.body_sha256
                or (previous_time is not None and utc(part.received_at) < previous_time)):
            raise WatchVoiceError("watch_voice_work_publication_conflict")
        previous_id, previous_time = part.comment_id, utc(part.received_at)
        snapshot.append((part.part, part.comment_id, part.writer, part.body_sha256,
                         part.issue_number, part.issue_url, iso(part.received_at)))
    return snapshot


def _receipt(result: WatchVoiceWorkResult, claim: WatchVoiceWorkClaim) -> dict[str, Any]:
    return {"schema": "watch-voice-work-result.v1", "event_id": result.note_id,
            "claim_id": claim.id, "epoch": result.epoch, "state": result.state,
            "expires_at": iso(claim.expires_at),
            "transcript_sha256": result.transcript_sha256,
            "characters": result.characters, "utf8_bytes": result.utf8_bytes,
            "prepared_at": iso(result.prepared_at), "saved_at": iso(result.saved_at),
            "accepted_at": iso(result.accepted_at), "start_recorded_now": False,
            "provider_authority": False}


async def _current(
    session: AsyncSession, note: WatchVoiceNote, actor_hash: str, token_hash: str,
) -> tuple[WatchVoiceWorkResult, WatchVoiceWorkClaim]:
    result = await session.get(WatchVoiceWorkResult, note.id)
    claim = await session.scalar(select(WatchVoiceWorkClaim).where(
        WatchVoiceWorkClaim.note_id == note.id,
        WatchVoiceWorkClaim.token_sha256 == token_hash,
    ))
    if (result is None or claim is None
            or not secrets.compare_digest(claim.actor_sha256, actor_hash)
            or result.claim_id != claim.id or result.epoch != claim.epoch):
        raise WatchVoiceError("watch_voice_work_claim_stale_or_denied")
    _pin(note, result)
    return result, claim


async def _expire(
    session: AsyncSession, note: WatchVoiceNote, result: WatchVoiceWorkResult,
    claim: WatchVoiceWorkClaim, now: datetime,
) -> bool:
    if claim.state not in {"claimed", "started"} or utc(claim.expires_at) > now:
        return False
    started = claim.state == "started"
    changed = await session.execute(update(WatchVoiceWorkClaim).where(
        WatchVoiceWorkClaim.id == claim.id, WatchVoiceWorkClaim.state == claim.state,
        WatchVoiceWorkClaim.epoch == result.epoch,
    ).values(state="unknown" if started else "expired"))
    if not changed.rowcount:  # type: ignore[attr-defined]
        return False
    result.state = "unknown" if started else "idle"
    result.last_error = "watch_voice_work_started_expired" if started else None
    if note.transcription_state == "processing":
        note.transcription_state = "unknown" if started else "received"
        note.last_error = result.last_error
        if not started:
            note.attempted_at = None
        else:
            note.processing_finished_at = now
    return True


async def claim_work(
    settings: Settings, factory: Factory, note_id: str, *, actor: str, token: str,
    revision: str, audio_sha256: str,
) -> dict[str, Any]:
    """Caller supplies a random opaque nonce; replay never creates another epoch."""
    _enabled(settings)
    key, actor_hash, token_hash = _credentials(note_id, actor, token)
    now = datetime.now(UTC)
    async with factory() as session:
        note = await _note(session, settings, key, original=True)
        if (revision, audio_sha256) != (note.revision, note.sha256):
            raise WatchVoiceError("watch_voice_work_source_conflict")
        result = await session.get(WatchVoiceWorkResult, key)
        previous = await session.scalar(select(WatchVoiceWorkClaim).where(
            WatchVoiceWorkClaim.note_id == key, WatchVoiceWorkClaim.token_sha256 == token_hash,
        ))
        if previous is not None:
            result, previous = await _current(session, note, actor_hash, token_hash)
            if await _expire(session, note, result, previous, now):
                await session.commit()
                raise WatchVoiceError("watch_voice_work_claim_expired")
            if previous.state in {"expired", "unknown"}:
                raise WatchVoiceError("watch_voice_work_claim_expired")
            if result.state in TERMINAL_TEXT:
                validate_work_candidate(note, result, previous)
            return _receipt(result, previous)
        if result is not None:
            _pin(note, result)
            if result.state in {"claimed", "started"} and result.claim_id:
                current_claim = await session.get(WatchVoiceWorkClaim, result.claim_id)
                if current_claim is not None and current_claim.epoch == result.epoch:
                    expired = await _expire(session, note, result, current_claim, now)
                    if expired and result.state == "unknown":
                        await session.commit()
            if result.state != "idle":
                raise WatchVoiceError("watch_voice_work_busy")
        source = await session.get(WatchVoiceSource, key)
        if (source is None or source.state != "archived" or source.card_state != "saved"
                or (source.workspace_key, source.account_id, source.file_id, source.revision,
                    source.size_bytes) != (note.workspace_key, note.account_id, note.file_id,
                                          note.revision, note.size_bytes)
                or not note.issue_number
                or note.issue_url != f"https://github.com/markmalina/mark/issues/{note.issue_number}"
                or (source.card_issue_number, source.card_issue_url)
                != (note.issue_number, note.issue_url)):
            raise WatchVoiceError("watch_voice_work_early_card_required")
        if not settings.gate_watch_voice_file_id:
            cursor = await session.get(WatchVoiceCursor,
                                      (note.workspace_key, note.account_id))
            cutoff = cursor.future_cutover_at if cursor else None
            if (cutoff is None or utc(note.dropbox_modified_at) <= utc(cutoff)
                    or utc(note.received_at) < utc(cutoff)
                    or utc(source.detected_at) < utc(cutoff)):
                raise WatchVoiceError("watch_voice_work_future_admission_required")
        if (len(note.data) != note.size_bytes
                or hashlib.sha256(note.data).hexdigest() != note.sha256):
            raise WatchVoiceError("watch_voice_work_original_integrity_failed")
        # Same atomic transition as Gate transcribe_note: only one may dispatch.
        changed = await session.execute(update(WatchVoiceNote).where(
            WatchVoiceNote.id == key, WatchVoiceNote.transcription_state == "received",
            WatchVoiceNote.transcript.is_(None), WatchVoiceNote.delivery_state == "pending",
            WatchVoiceNote.next_part == 0,
            func.coalesce(WatchVoiceNote.processing_attempt_count, 0) == 0,
            func.coalesce(WatchVoiceNote.provider_request_count, 0) == 0,
        ).values(transcription_state="processing", attempted_at=now))
        if not changed.rowcount:  # type: ignore[attr-defined]
            raise WatchVoiceError("watch_voice_work_busy")
        if result is None:
            result = WatchVoiceWorkResult(note_id=key, revision=note.revision,
                                         audio_sha256=note.sha256, epoch=0, state="idle",
                                         issue_number=note.issue_number, issue_url=note.issue_url)
            session.add(result)
        result.epoch += 1
        claim = WatchVoiceWorkClaim(
            id=str(uuid.uuid4()), note_id=key, epoch=result.epoch,
            actor_sha256=actor_hash, token_sha256=token_hash, state="claimed",
            created_at=now, expires_at=now + timedelta(
                seconds=settings.gate_watch_voice_work_lease_seconds),
        )
        session.add(claim)
        result.state, result.claim_id, result.last_error = "claimed", claim.id, None
        await session.commit()
        return _receipt(result, claim)


async def start_work(
    settings: Settings, factory: Factory, note_id: str, *, actor: str, token: str,
) -> dict[str, Any]:
    """Durably record start before external Work processing; grants no provider call."""
    _enabled(settings)
    key, actor_hash, token_hash = _credentials(note_id, actor, token)
    async with factory() as session:
        note = await _note(session, settings, key)
        result, claim = await _current(session, note, actor_hash, token_hash)
        now = datetime.now(UTC)
        if await _expire(session, note, result, claim, now):
            await session.commit()
            raise WatchVoiceError("watch_voice_work_claim_expired")
        if claim.state == "started" and result.state == "started":
            return _receipt(result, claim)
        if claim.state != "claimed" or result.state != "claimed":
            raise WatchVoiceError("watch_voice_work_claim_stale_or_denied")
        changed = await session.execute(update(WatchVoiceWorkClaim).where(
            WatchVoiceWorkClaim.id == claim.id, WatchVoiceWorkClaim.state == "claimed",
            WatchVoiceWorkClaim.expires_at > now,
        ).values(state="started", started_at=now)
          .execution_options(synchronize_session="fetch"))
        if not changed.rowcount:  # type: ignore[attr-defined]
            raise WatchVoiceError("watch_voice_work_claim_stale_or_denied")
        result.state = "started"
        await session.commit()
        receipt = _receipt(result, claim)
        receipt["start_recorded_now"] = True
        return receipt


async def submit_work(
    settings: Settings, factory: Factory, note_id: str, *, actor: str, token: str,
    revision: str, audio_sha256: str, text: str, transcript_sha256: str,
) -> dict[str, Any]:
    _enabled(settings)
    key, actor_hash, token_hash = _credentials(note_id, actor, token)
    if not isinstance(text, str) or not 1 <= len(text) <= 100_000:
        raise WatchVoiceError("watch_voice_work_text_invalid")
    try:
        encoded = text.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        raise WatchVoiceError("watch_voice_work_text_invalid") from None
    if (not text.strip() or len(text) > 100_000 or len(encoded) > 400_000
            or hashlib.sha256(encoded).hexdigest() != transcript_sha256):
        raise WatchVoiceError("watch_voice_work_text_invalid")
    async with factory() as session:
        note = await _note(session, settings, key)
        result, claim = await _current(session, note, actor_hash, token_hash)
        if (revision, audio_sha256) != (result.revision, result.audio_sha256):
            raise WatchVoiceError("watch_voice_work_source_conflict")
        if result.state in TERMINAL_TEXT:
            validate_work_candidate(note, result, claim)
            if result.transcript_sha256 != transcript_sha256 or result.transcript != text:
                raise WatchVoiceError("watch_voice_work_candidate_immutable")
            if note.transcript_origin != "work" or note.transcript != text:
                raise WatchVoiceError("watch_voice_work_publication_conflict")
            return _receipt(result, claim)
        now = datetime.now(UTC)
        if await _expire(session, note, result, claim, now):
            await session.commit()
            raise WatchVoiceError("watch_voice_work_claim_expired")
        if (claim.state != "started" or result.state != "started"
                or note.transcription_state != "processing" or note.transcript
                or note.next_part != 0 or note.delivery_state != "pending"):
            raise WatchVoiceError("watch_voice_work_claim_stale_or_denied")
        result.transcript, result.transcript_sha256 = text, transcript_sha256
        result.characters, result.utf8_bytes = len(text), len(encoded)
        result.state, result.prepared_at, result.last_error = "prepared", now, None
        claim.state, claim.submitted_at = "submitted", now
        note.transcription_state, note.transcript = "transcribed", text
        note.transcript_origin = "work"
        note.model = None  # No claim about the external recognizer/provider/model/cost.
        note.transcribed_at = note.processing_finished_at = now
        note.last_error = None
        await session.commit()
        return _receipt(result, claim)


async def expire_work_claims(settings: Settings, factory: Factory) -> None:
    """Honor existing fences even when admission is subsequently disabled."""
    async with factory() as session:
        keys = list((await session.scalars(select(WatchVoiceWorkResult.note_id)
            .join(WatchVoiceNote, WatchVoiceNote.id == WatchVoiceWorkResult.note_id)
            .join(WatchVoiceWorkClaim, WatchVoiceWorkClaim.id == WatchVoiceWorkResult.claim_id)
            .where(WatchVoiceNote.workspace_key == settings.gate_workspace_id,
                   WatchVoiceNote.account_id == settings.dropbox_call_account_id,
                   WatchVoiceWorkResult.state.in_(("claimed", "started")),
                   WatchVoiceWorkClaim.expires_at <= datetime.now(UTC))
            .order_by(WatchVoiceWorkClaim.expires_at, WatchVoiceWorkResult.note_id)
            .limit(50))).all())
    for key in keys:
        async with factory() as session:
            # Do not apply a new exact-file selector to cleanup of an existing lease.
            note = await session.scalar(select(WatchVoiceNote).options(defer(WatchVoiceNote.data))
                .where(WatchVoiceNote.id == key,
                       WatchVoiceNote.workspace_key == settings.gate_workspace_id,
                       WatchVoiceNote.account_id == settings.dropbox_call_account_id)
                .with_for_update())
            result = await session.get(WatchVoiceWorkResult, key)
            if note is None or result is None or not result.claim_id:
                continue
            _pin(note, result)
            claim = await session.get(WatchVoiceWorkClaim, result.claim_id)
            if claim is not None and claim.epoch == result.epoch:
                await _expire(session, note, result, claim, datetime.now(UTC))
                await session.commit()


async def record_text_receipt(
    session: AsyncSession, note: WatchVoiceNote, receipt: GithubReceipt, body: str,
) -> None:
    """Called in the same transaction as a successful Note part-advance CAS."""
    result = await session.get(WatchVoiceWorkResult, note.id)
    if result is None:
        raise WatchVoiceError("watch_voice_work_publication_conflict")
    await _candidate(session, note, result)
    _pin(note, result)
    if (result.state != "prepared" or not note.transcript or not note.delivery_writer
            or body_digest(note.transcript) != result.transcript_sha256
            or (note.issue_number, note.issue_url) != (receipt.number, receipt.url)
            or type(receipt.comment_id) is not int or not 0 < receipt.comment_id <= 2**63 - 1):
        raise WatchVoiceError("watch_voice_work_publication_conflict")
    existing = await session.get(WatchVoiceTextReceipt, (note.id, note.next_part))
    expected = (note.delivery_writer, body_digest(body), receipt.number, receipt.url,
                receipt.comment_id)
    if existing is not None:
        if (existing.writer, existing.body_sha256, existing.issue_number,
                existing.issue_url, existing.comment_id) != expected:
            raise WatchVoiceError("watch_voice_work_publication_conflict")
        return
    session.add(WatchVoiceTextReceipt(
        note_id=note.id, part=note.next_part, writer=expected[0], body_sha256=expected[1],
        issue_number=receipt.number, issue_url=receipt.url, received_at=receipt.received_at,
        comment_id=receipt.comment_id,
    ))


async def validate_work_publication(factory: Factory, note: WatchVoiceNote) -> None:
    """Reject a stale/tampered Work payload before any publication I/O."""
    async with factory() as session:
        result = await session.get(WatchVoiceWorkResult, note.id)
        current = await session.scalar(select(WatchVoiceNote).options(defer(WatchVoiceNote.data))
                                       .where(WatchVoiceNote.id == note.id))
        if result is None or current is None:
            raise WatchVoiceError("watch_voice_work_publication_conflict")
        await _candidate(session, current, result)
        await _candidate(session, note, result)
        _pin(current, result)
        _pin(note, result)
        if (result.state != "prepared" or not result.transcript
                or note.transcript != result.transcript or current.transcript != result.transcript
                or body_digest(result.transcript) != result.transcript_sha256
                or current.transcript_origin != "work"
                or current.transcription_state != "transcribed"
                or current.next_part != note.next_part
                or current.delivery_state != note.delivery_state):
            raise WatchVoiceError("watch_voice_work_publication_conflict")


async def accept_work_result(factory: Factory, client: MarkInboxClient, key: str) -> bool:
    """No publication POST here; accepted follows a full exact readback and fenced CAS."""
    from app.worker.watch_voice import issue_body

    async with factory() as session:
        result = await session.get(WatchVoiceWorkResult, key)
        if result is None or result.state not in {"prepared", "saved"}:
            return False
        note = await session.scalar(select(WatchVoiceNote).options(defer(WatchVoiceNote.data))
                                    .where(WatchVoiceNote.id == key))
        if note is None:
            return False
        _pin(note, result)
        fingerprint = await _candidate(session, note, result)
        await session.execute(update(WatchVoiceWorkResult).where(
            WatchVoiceWorkResult.note_id == key,
            WatchVoiceWorkResult.epoch == result.epoch,
            WatchVoiceWorkResult.state.in_(("prepared", "saved")),
        ).values(readback_attempted_at=datetime.now(UTC)))
        if (note.transcription_state != "transcribed" or note.transcript_origin != "work"
                or not note.transcript or body_digest(note.transcript) != result.transcript_sha256
                or note.delivery_state != "delivered" or not note.delivered_at
                or not note.issue_number or not note.issue_url
                or note.next_part != len(transcript_parts(note.transcript))):
            result.last_error = "watch_voice_work_text_not_complete"
            await session.commit()
            return False
        parts = list((await session.scalars(select(WatchVoiceTextReceipt)
                    .where(WatchVoiceTextReceipt.note_id == key)
                    .order_by(WatchVoiceTextReceipt.part))).all())
        if [p.part for p in parts] != list(range(note.next_part)):
            result.last_error = "watch_voice_work_receipts_incomplete"
            await session.commit()
            return False
        try:
            ledger_snapshot = validate_work_ledger(note, parts)
        except WatchVoiceError as exc:
            result.last_error = exc.code
            await session.commit()
            return False
        epoch, digest = result.epoch, result.transcript_sha256
        await session.execute(update(WatchVoiceWorkResult).where(
            WatchVoiceWorkResult.note_id == key, WatchVoiceWorkResult.epoch == epoch,
            WatchVoiceWorkResult.state.in_(("prepared", "saved")),
        ).values(state="saved", saved_at=result.saved_at or datetime.now(UTC)))
        saved_at = iso(result.saved_at)
        await session.commit()
    try:
        previous_received: datetime | None = None
        for part in parts:
            body = issue_body(note, part.part, publication_part=part.part + 1)
            if (body_digest(body) != part.body_sha256
                    or (part.issue_number, part.issue_url) != (note.issue_number, note.issue_url)):
                raise WatchVoiceError("watch_voice_work_publication_conflict")
            receipt = await client.reconcile(part=part.part + 1, issue_number=note.issue_number,
                                             body=body, expected_writer=part.writer)
            if (receipt is None
                    or (receipt.number, receipt.url) != (note.issue_number, note.issue_url)
                    or receipt.comment_id != part.comment_id):
                raise WatchVoiceError("watch_voice_work_readback_incomplete")
            if (utc(receipt.received_at) != utc(part.received_at)
                    or (previous_received is not None
                        and utc(receipt.received_at) < previous_received)):
                raise WatchVoiceError("watch_voice_work_publication_conflict")
            previous_received = utc(receipt.received_at)
    except WatchVoiceError as exc:
        async with factory() as session:
            await session.execute(update(WatchVoiceWorkResult).where(
                WatchVoiceWorkResult.note_id == key, WatchVoiceWorkResult.epoch == epoch,
                WatchVoiceWorkResult.state == "saved",
            ).values(last_error=exc.code))
            await session.commit()
        return False
    async with factory() as session:
        current = await session.scalar(select(WatchVoiceNote).options(defer(WatchVoiceNote.data))
                                       .where(WatchVoiceNote.id == key).with_for_update())
        if (current is None or current.transcript_origin != "work" or not current.transcript
                or current.transcription_state != "transcribed"
                or body_digest(current.transcript) != digest
                or current.delivery_state != "delivered" or not current.delivered_at
                or current.next_part != note.next_part
                or (current.revision, current.sha256, current.issue_number, current.issue_url)
                != (note.revision, note.sha256, note.issue_number, note.issue_url)):
            return False
        fresh = await session.scalar(select(WatchVoiceWorkResult).where(
            WatchVoiceWorkResult.note_id == key).with_for_update())
        if fresh is None or fresh.state != "saved":
            return False
        claim = await session.scalar(select(WatchVoiceWorkClaim).where(
            WatchVoiceWorkClaim.id == fresh.claim_id).with_for_update())
        if (validate_work_candidate(current, fresh, claim) != fingerprint
                or iso(fresh.saved_at) != saved_at):
            raise WatchVoiceError("watch_voice_work_candidate_conflict")
        final_parts = list((await session.scalars(select(WatchVoiceTextReceipt).where(
            WatchVoiceTextReceipt.note_id == key).order_by(WatchVoiceTextReceipt.part)
            .with_for_update())).all())
        if validate_work_ledger(current, final_parts) != ledger_snapshot:
            raise WatchVoiceError("watch_voice_work_candidate_conflict")
        changed = await session.execute(update(WatchVoiceWorkResult).where(
            WatchVoiceWorkResult.note_id == key, WatchVoiceWorkResult.epoch == epoch,
            WatchVoiceWorkResult.transcript_sha256 == digest,
            WatchVoiceWorkResult.state == "saved",
        ).values(state="accepted", accepted_at=datetime.now(UTC), last_error=None))
        await session.commit()
        return bool(changed.rowcount)  # type: ignore[attr-defined]


async def drain_work_receipts(
    settings: Settings, factory: Factory, client: MarkInboxClient,
    cutover: datetime | None = None,
) -> None:
    query = select(WatchVoiceNote.id).join(WatchVoiceWorkResult,
        WatchVoiceWorkResult.note_id == WatchVoiceNote.id).where(
        WatchVoiceNote.workspace_key == settings.gate_workspace_id,
        WatchVoiceNote.account_id == settings.dropbox_call_account_id,
        WatchVoiceWorkResult.state.in_(("prepared", "saved")),
        WatchVoiceNote.delivery_state == "delivered",
    )
    if settings.gate_watch_voice_file_id:
        query = query.where(WatchVoiceNote.file_id == settings.gate_watch_voice_file_id,
                            WatchVoiceNote.revision == settings.gate_watch_voice_revision)
    if cutover is not None:
        query = query.where(WatchVoiceNote.received_at >= cutover,
                            WatchVoiceNote.dropbox_modified_at > cutover)
    async with factory() as session:
        keys = list((await session.scalars(query.order_by(
            case((WatchVoiceWorkResult.readback_attempted_at.is_(None), 0), else_=1),
            WatchVoiceWorkResult.readback_attempted_at, WatchVoiceNote.id,
        ).limit(10))).all())
    for key in keys:
        await accept_work_result(factory, client, key)
