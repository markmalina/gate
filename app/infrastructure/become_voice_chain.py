"""Durable provider-disabled BECOME voice correlation and chain slots."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)
from app.application.become_media_intake import (
    CONFIRMED_TRANSCRIPT_RETENTION,
)
from app.application.become_voice_chain import (
    BECOME_CHAIN_SLOT_SCHEMA,
    BECOME_CHAIN_STAGES,
    BECOME_VOICE_CORRELATION_SCHEMA,
    BecomeChainSlotState,
    BecomeChainStage,
    ConfirmedBecomeTranscriptReceipt,
    FinalizedBecomeChainStage,
    JsonObject,
    PreparedBecomeVoiceCorrelation,
    ReservedBecomeChainStage,
    canonical_payload_digest,
)
from app.models.become import (
    BecomeMediaIntakeModel,
    BecomeRelationsConsentHandoffModel,
    BecomeVoiceChainSlotModel,
    BecomeVoiceCorrelationModel,
)
from app.models.canonical import Workspace


class SessionFactoryBecomeVoiceChainStore:
    """Create roots, confirm transcripts, and enforce exact stage replay."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def prepare(
        self,
        *,
        workspace_key: str,
        intake_key: str,
        prepared_at: datetime,
    ) -> PreparedBecomeVoiceCorrelation:
        if prepared_at.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_voice_correlation_timestamp_invalid"
            )
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        Workspace,
                        BecomeMediaIntakeModel,
                        BecomeRelationsConsentHandoffModel,
                    )
                    .join(
                        BecomeMediaIntakeModel,
                        BecomeMediaIntakeModel.workspace_id == Workspace.id,
                    )
                    .join(
                        BecomeRelationsConsentHandoffModel,
                        BecomeRelationsConsentHandoffModel.id
                        == BecomeMediaIntakeModel.relations_handoff_id,
                    )
                    .where(
                        Workspace.key == workspace_key,
                        Workspace.is_active.is_(True),
                        BecomeMediaIntakeModel.intake_key == intake_key,
                        BecomeMediaIntakeModel.kind == "voice",
                        BecomeMediaIntakeModel.status.in_(
                            ("archived", "raw_purged")
                        ),
                        BecomeRelationsConsentHandoffModel.status == "executed",
                        BecomeRelationsConsentHandoffModel.execution_digest
                        .is_not(None),
                    )
                    .with_for_update(of=BecomeMediaIntakeModel)
                )
            ).one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_voice_correlation_archive_required"
                )
            workspace, intake, handoff = row
            intake_digest = _intake_digest(intake, handoff)
            correlation_ref = _correlation_ref(
                workspace_id=workspace.id,
                intake_id=intake.id,
                intake_digest=intake_digest,
            )
            correlation_id = uuid.uuid4()
            inserted = await session.scalar(
                insert(BecomeVoiceCorrelationModel)
                .values(
                    id=correlation_id,
                    workspace_id=workspace.id,
                    media_intake_id=intake.id,
                    correlation_ref=correlation_ref,
                    schema_name=BECOME_VOICE_CORRELATION_SCHEMA,
                    intake_digest=intake_digest,
                    created_at=prepared_at,
                )
                .on_conflict_do_nothing(
                    index_elements=["media_intake_id"]
                )
                .returning(BecomeVoiceCorrelationModel.id)
            )
            duplicate = inserted is None
            if inserted is None:
                existing = await session.scalar(
                    select(BecomeVoiceCorrelationModel).where(
                        BecomeVoiceCorrelationModel.media_intake_id
                        == intake.id
                    )
                )
                if (
                    existing is None
                    or existing.workspace_id != workspace.id
                    or existing.correlation_ref != correlation_ref
                    or existing.intake_digest != intake_digest
                    or existing.schema_name
                    != BECOME_VOICE_CORRELATION_SCHEMA
                ):
                    raise BecomeControlledIntakeError(
                        "become_voice_correlation_mutated_replay"
                    )
                correlation_id = existing.id
            for position, stage in enumerate(BECOME_CHAIN_STAGES, start=1):
                await session.execute(
                    insert(BecomeVoiceChainSlotModel)
                    .values(
                        workspace_id=workspace.id,
                        correlation_id=correlation_id,
                        stage=stage,
                        position=position,
                        schema_name=BECOME_CHAIN_SLOT_SCHEMA,
                        state=BecomeChainSlotState.BLOCKED.value,
                        created_at=prepared_at,
                        updated_at=prepared_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["correlation_id", "stage"]
                    )
                )
            slots = (
                await session.scalars(
                    select(BecomeVoiceChainSlotModel)
                    .where(
                        BecomeVoiceChainSlotModel.correlation_id
                        == correlation_id
                    )
                    .order_by(BecomeVoiceChainSlotModel.position)
                )
            ).all()
            _validate_slot_identity(slots)
            return PreparedBecomeVoiceCorrelation(
                correlation_ref=correlation_ref,
                slot_count=len(slots),
                duplicate=duplicate,
            )

    async def reconcile_one(
        self,
        *,
        workspace_key: str,
        prepared_at: datetime,
    ) -> PreparedBecomeVoiceCorrelation | None:
        """Materialize one archived voice missed before this projector existed."""

        async with self._sessions() as session:
            intake_key = await session.scalar(
                select(BecomeMediaIntakeModel.intake_key)
                .join(
                    Workspace,
                    Workspace.id == BecomeMediaIntakeModel.workspace_id,
                )
                .outerjoin(
                    BecomeVoiceCorrelationModel,
                    BecomeVoiceCorrelationModel.media_intake_id
                    == BecomeMediaIntakeModel.id,
                )
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                    BecomeMediaIntakeModel.kind == "voice",
                    BecomeMediaIntakeModel.status.in_(
                        ("archived", "raw_purged")
                    ),
                    BecomeVoiceCorrelationModel.id.is_(None),
                )
                .order_by(BecomeMediaIntakeModel.received_at)
                .limit(1)
            )
        if intake_key is None:
            return None
        return await self.prepare(
            workspace_key=workspace_key,
            intake_key=str(intake_key),
            prepared_at=prepared_at,
        )

    async def confirm_transcript(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        transcript: str,
        confirmed_at: datetime,
    ) -> ConfirmedBecomeTranscriptReceipt:
        normalized = transcript.strip()
        if (
            confirmed_at.tzinfo is None
            or not normalized
            or normalized != transcript
            or len(normalized) > 100_000
        ):
            raise BecomeControlledIntakeError(
                "become_transcript_confirmation_invalid"
            )
        transcript_digest = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
        expires_at = confirmed_at + CONFIRMED_TRANSCRIPT_RETENTION
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        BecomeVoiceCorrelationModel,
                        BecomeMediaIntakeModel,
                    )
                    .join(
                        Workspace,
                        Workspace.id
                        == BecomeVoiceCorrelationModel.workspace_id,
                    )
                    .join(
                        BecomeMediaIntakeModel,
                        BecomeMediaIntakeModel.id
                        == BecomeVoiceCorrelationModel.media_intake_id,
                    )
                    .where(
                        Workspace.key == workspace_key,
                        Workspace.is_active.is_(True),
                        BecomeVoiceCorrelationModel.correlation_ref
                        == correlation_ref,
                    )
                    .with_for_update(
                        of=(
                            BecomeVoiceCorrelationModel,
                            BecomeMediaIntakeModel,
                        )
                    )
                )
            ).one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_voice_correlation_not_found"
                )
            correlation, intake = row
            if intake.status not in {"archived", "raw_purged"}:
                raise BecomeControlledIntakeError(
                    "become_transcript_raw_archive_required"
                )
            duplicate = intake.transcript_state == "confirmed"
            if intake.transcript_state == "none":
                intake.transcript_state = "confirmed"
                intake.confirmed_transcript = normalized
                intake.transcript_digest = transcript_digest
                intake.transcript_confirmed_at = confirmed_at
                intake.transcript_expires_at = expires_at
                intake.updated_at = confirmed_at
            elif (
                intake.transcript_state != "confirmed"
                or intake.confirmed_transcript != normalized
                or intake.transcript_digest != transcript_digest
                or intake.transcript_confirmed_at != confirmed_at
                or intake.transcript_expires_at != expires_at
            ):
                raise BecomeControlledIntakeError(
                    "become_transcript_mutated_replay"
                )

            slots = (
                await session.scalars(
                    select(BecomeVoiceChainSlotModel)
                    .where(
                        BecomeVoiceChainSlotModel.correlation_id
                        == correlation.id
                    )
                    .order_by(BecomeVoiceChainSlotModel.position)
                    .with_for_update()
                )
            ).all()
            _validate_slot_identity(slots)
            relations = slots[0]
            if relations.state == BecomeChainSlotState.BLOCKED.value:
                relations.state = BecomeChainSlotState.READY.value
                relations.updated_at = confirmed_at
            elif relations.state not in {
                BecomeChainSlotState.READY.value,
                BecomeChainSlotState.REQUESTED.value,
                BecomeChainSlotState.COMPLETED.value,
            }:
                raise BecomeControlledIntakeError(
                    "become_transcript_relations_slot_invalid"
                )
            return ConfirmedBecomeTranscriptReceipt(
                correlation_ref=correlation.correlation_ref,
                transcript_digest=transcript_digest,
                transcript_expires_at=expires_at,
                relations_slot_state=BecomeChainSlotState.READY,
                duplicate=duplicate,
            )

    async def reserve_stage(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        stage: BecomeChainStage,
        request_payload: JsonObject,
        source_sha: str,
        mapping_digest: str | None,
        requested_at: datetime,
    ) -> ReservedBecomeChainStage:
        request_digest = canonical_payload_digest(request_payload)
        async with self._sessions.begin() as session:
            slot = await _locked_slot(
                session,
                workspace_key=workspace_key,
                correlation_ref=correlation_ref,
                stage=stage,
            )
            if slot.state == BecomeChainSlotState.BLOCKED.value:
                raise BecomeControlledIntakeError(
                    "become_chain_stage_blocked"
                )
            if slot.state == BecomeChainSlotState.READY.value:
                slot.state = BecomeChainSlotState.REQUESTED.value
                slot.request_digest = request_digest
                slot.request_payload = request_payload
                slot.source_sha = source_sha
                slot.mapping_digest = mapping_digest
                slot.updated_at = requested_at
                return _reservation(
                    slot,
                    correlation_ref=correlation_ref,
                    duplicate=False,
                )
            if (
                slot.request_digest != request_digest
                or slot.request_payload != request_payload
                or slot.source_sha != source_sha
                or slot.mapping_digest != mapping_digest
            ):
                raise BecomeControlledIntakeError(
                    "become_chain_mutated_request_replay"
                )
            return _reservation(
                slot,
                correlation_ref=correlation_ref,
                duplicate=True,
            )

    async def finalize_stage(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        stage: BecomeChainStage,
        request_digest: str,
        receipt_payload: JsonObject,
        state: BecomeChainSlotState,
        finished_at: datetime,
    ) -> FinalizedBecomeChainStage:
        receipt_digest = canonical_payload_digest(receipt_payload)
        async with self._sessions.begin() as session:
            slot = await _locked_slot(
                session,
                workspace_key=workspace_key,
                correlation_ref=correlation_ref,
                stage=stage,
            )
            if slot.request_digest != request_digest:
                raise BecomeControlledIntakeError(
                    "become_chain_receipt_request_mismatch"
                )
            duplicate = slot.state != BecomeChainSlotState.REQUESTED.value
            if not duplicate:
                slot.state = state.value
                slot.receipt_digest = receipt_digest
                slot.receipt_payload = receipt_payload
                slot.terminal_at = finished_at
                slot.updated_at = finished_at
                if state is BecomeChainSlotState.COMPLETED:
                    await _advance_next_slot(
                        session,
                        slot=slot,
                        advanced_at=finished_at,
                    )
            elif (
                slot.state != state.value
                or slot.receipt_digest != receipt_digest
                or slot.receipt_payload != receipt_payload
            ):
                raise BecomeControlledIntakeError(
                    "become_chain_mutated_receipt_replay"
                )
            return FinalizedBecomeChainStage(
                correlation_ref=correlation_ref,
                stage=stage,
                state=state,
                request_digest=request_digest,
                receipt_digest=receipt_digest,
                duplicate=duplicate,
            )


async def become_voice_chain_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, object]:
    root_count = int(
        (
            await session.scalar(
                select(func.count(BecomeVoiceCorrelationModel.id))
                .join(
                    Workspace,
                    Workspace.id
                    == BecomeVoiceCorrelationModel.workspace_id,
                )
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
            )
        )
        or 0
    )
    rows = (
        await session.execute(
            select(
                BecomeVoiceChainSlotModel.stage,
                BecomeVoiceChainSlotModel.state,
                func.count(BecomeVoiceChainSlotModel.id),
            )
            .join(
                Workspace,
                Workspace.id == BecomeVoiceChainSlotModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
            )
            .group_by(
                BecomeVoiceChainSlotModel.stage,
                BecomeVoiceChainSlotModel.state,
            )
        )
    ).all()
    counts = {
        (str(stage), str(state)): int(count)
        for stage, state, count in rows
    }
    failure_payload = await session.scalar(
        select(BecomeVoiceChainSlotModel.receipt_payload)
        .join(
            Workspace,
            Workspace.id == BecomeVoiceChainSlotModel.workspace_id,
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            BecomeVoiceChainSlotModel.state
            == BecomeChainSlotState.FAILED.value,
        )
        .order_by(BecomeVoiceChainSlotModel.terminal_at.desc())
        .limit(1)
    )
    failure_code: str | None = None
    if isinstance(failure_payload, dict):
        value = failure_payload.get("error_code")
        if isinstance(value, str):
            failure_code = value
    result: dict[str, object] = {
        "voice_correlation_count": root_count,
        "chain_slot_count": sum(counts.values()),
        "chain_ready_count": sum(
            count
            for (stage, state), count in counts.items()
            if state == BecomeChainSlotState.READY.value
        ),
        "chain_requested_count": sum(
            count
            for (stage, state), count in counts.items()
            if state == BecomeChainSlotState.REQUESTED.value
        ),
        "chain_unknown_count": sum(
            count
            for (stage, state), count in counts.items()
            if state == BecomeChainSlotState.UNKNOWN.value
        ),
        "chain_failed_count": sum(
            count
            for (stage, state), count in counts.items()
            if state == BecomeChainSlotState.FAILED.value
        ),
        "chain_last_failure_code": failure_code,
    }
    for stage in BECOME_CHAIN_STAGES:
        result[f"{stage}_chain_completed_count"] = counts.get(
            (stage, BecomeChainSlotState.COMPLETED.value),
            0,
        )
    return result


async def _locked_slot(
    session: AsyncSession,
    *,
    workspace_key: str,
    correlation_ref: str,
    stage: BecomeChainStage,
) -> BecomeVoiceChainSlotModel:
    slot = await session.scalar(
        select(BecomeVoiceChainSlotModel)
        .join(
            BecomeVoiceCorrelationModel,
            BecomeVoiceCorrelationModel.id
            == BecomeVoiceChainSlotModel.correlation_id,
        )
        .join(
            Workspace,
            Workspace.id == BecomeVoiceChainSlotModel.workspace_id,
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            BecomeVoiceCorrelationModel.correlation_ref
            == correlation_ref,
            BecomeVoiceChainSlotModel.stage == stage.value,
        )
        .with_for_update()
    )
    if slot is None:
        raise BecomeControlledIntakeError(
            "become_chain_slot_not_found"
        )
    return slot


async def _advance_next_slot(
    session: AsyncSession,
    *,
    slot: BecomeVoiceChainSlotModel,
    advanced_at: datetime,
) -> None:
    if slot.position >= len(BECOME_CHAIN_STAGES):
        return
    next_slot = await session.scalar(
        select(BecomeVoiceChainSlotModel)
        .where(
            BecomeVoiceChainSlotModel.correlation_id
            == slot.correlation_id,
            BecomeVoiceChainSlotModel.position == slot.position + 1,
        )
        .with_for_update()
    )
    if (
        next_slot is None
        or next_slot.state != BecomeChainSlotState.BLOCKED.value
    ):
        raise BecomeControlledIntakeError(
            "become_chain_next_slot_invalid"
        )
    next_slot.state = BecomeChainSlotState.READY.value
    next_slot.updated_at = advanced_at


def _intake_digest(
    intake: BecomeMediaIntakeModel,
    handoff: BecomeRelationsConsentHandoffModel,
) -> str:
    if intake.raw_sha256 is None or handoff.execution_digest is None:
        raise BecomeControlledIntakeError(
            "become_voice_correlation_digest_source_missing"
        )
    source = ":".join(
        (
            intake.intake_key,
            intake.update_ref,
            intake.file_unique_digest,
            intake.raw_sha256,
            handoff.execution_digest,
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _correlation_ref(
    *,
    workspace_id: uuid.UUID,
    intake_id: uuid.UUID,
    intake_digest: str,
) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}:{intake_id}:{intake_digest}".encode()
    ).hexdigest()
    return f"synthetic://correlation/voice/{digest[:32]}"


def _validate_slot_identity(
    slots: Sequence[BecomeVoiceChainSlotModel],
) -> None:
    identities = [
        (slot.stage, slot.position, slot.schema_name)
        for slot in slots
    ]
    expected = [
        (stage, position, BECOME_CHAIN_SLOT_SCHEMA)
        for position, stage in enumerate(BECOME_CHAIN_STAGES, start=1)
    ]
    if identities != expected:
        raise BecomeControlledIntakeError(
            "become_chain_slot_identity_invalid"
        )


def _reservation(
    slot: BecomeVoiceChainSlotModel,
    *,
    correlation_ref: str,
    duplicate: bool,
) -> ReservedBecomeChainStage:
    if (
        slot.request_digest is None
        or slot.request_payload is None
        or slot.source_sha is None
    ):
        raise BecomeControlledIntakeError(
            "become_chain_request_not_reserved"
        )
    state = BecomeChainSlotState(slot.state)
    return ReservedBecomeChainStage(
        correlation_ref=correlation_ref,
        stage=BecomeChainStage(slot.stage),
        state=state,
        request_digest=slot.request_digest,
        source_sha=slot.source_sha,
        duplicate=duplicate,
        reconciliation_required=state
        in {
            BecomeChainSlotState.REQUESTED,
            BecomeChainSlotState.UNKNOWN,
        },
        request_payload=slot.request_payload,
        receipt_digest=slot.receipt_digest,
        receipt_payload=slot.receipt_payload,
    )
