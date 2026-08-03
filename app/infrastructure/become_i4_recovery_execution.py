"""Append-only one-attempt executor for BECOME I4 recovery version 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.application.become_i4_chain import (
    I4_CONTRACT_DIGEST,
    BecomeI4CorrelationEvidence,
    BecomeI4Stage,
    JsonObject,
    build_mayaki_request,
    build_oko_request,
    build_take_request,
    canonical_digest,
    project_i4_refs,
    validate_i4_receipt,
)
from app.domain.become_controlled_intake import BecomeChainSlotState
from app.infrastructure.become_i4_chain import (
    MAYAKI_SOURCE_SHA,
    OKO_SOURCE_SHA,
    TAKE_SOURCE_SHA,
    BecomeI4HttpClient,
    BecomeI4ProductionTargets,
    BecomeI4TransportState,
    PostReservedBecomeI4StageOnce,
    ReservedBecomeI4Stage,
)
from app.infrastructure.become_i4_recovery import (
    validate_recovery_event_sequence,
    validate_recovery_replay,
)
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeMediaIntakeModel,
    BecomeRelationsConsentHandoffModel,
    BecomeVoiceChainSlotModel,
    BecomeVoiceCorrelationModel,
)
from app.models.become_recovery import (
    BecomeI4RecoveryAttemptModel,
    BecomeI4RecoveryReceiptModel,
    BecomeI4RecoveryStageEventModel,
)
from app.models.canonical import Workspace

ATTEMPT_SCHEMA = "gate.become-i4-recovery-attempt@2"
EVENT_SCHEMA = "gate.become-i4-recovery-stage-event@2"
RECOVERY_STAGES = (
    BecomeI4Stage.MAYAKI,
    BecomeI4Stage.OKO,
    BecomeI4Stage.TAKE,
)
_STAGE_ORDINAL = {
    BecomeI4Stage.MAYAKI: 2,
    BecomeI4Stage.OKO: 3,
    BecomeI4Stage.TAKE: 4,
}
_ATTEMPT_FIELDS = frozenset(
    {
        "schema",
        "attempt_ref",
        "attempt_version",
        "correlation_ref",
        "prepared_recovery_digest",
        "original_failed_request_digest",
        "original_failed_receipt_digest",
        "relations_receipt_digest",
        "transcript_digest",
        "contract_digest",
        "source_shas",
        "stage_order",
        "max_posts",
        "state",
        "external_effects",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "attempt_digest",
        "attempt_version",
        "stage",
        "ordinal",
        "event_sequence",
        "event_type",
        "request_digest",
        "source_sha",
        "receipt_digest",
        "external_effects",
    }
)


@dataclass(frozen=True, slots=True)
class BecomeI4RecoveryExecutionResult:
    disposition: str
    stage: BecomeI4Stage | None = None
    attempted: bool = False
    reconciliation_required: bool = False


@dataclass(frozen=True, slots=True)
class _RecoveryCandidate:
    attempt: BecomeI4RecoveryAttemptModel
    evidence: BecomeI4CorrelationEvidence
    failed_slot: BecomeVoiceChainSlotModel
    relations_slot: BecomeVoiceChainSlotModel
    events: tuple[BecomeI4RecoveryStageEventModel, ...]


class SessionFactoryBecomeI4RecoveryExecutor:
    """Execute at most one newly reserved recovery stage per worker pass."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        http_client: BecomeI4HttpClient,
        enabled: bool = False,
        max_posts: int = 0,
        targets: BecomeI4ProductionTargets | None = None,
    ) -> None:
        if max_posts < 0 or max_posts > 3:
            raise ValueError("bounded_recovery_max_posts_required")
        self._sessions = sessions
        self._enabled = enabled
        self._max_posts = max_posts
        self._targets = targets or BecomeI4ProductionTargets()
        self._transport = PostReservedBecomeI4StageOnce(http_client)

    async def run_once(
        self,
        *,
        workspace_key: str,
        now: datetime,
    ) -> BecomeI4RecoveryExecutionResult:
        if not self._enabled or self._max_posts == 0:
            return BecomeI4RecoveryExecutionResult(disposition="disabled")

        async with self._sessions() as session:
            candidate = await _load_candidate(session, workspace_key=workspace_key)
        if candidate is None:
            return BecomeI4RecoveryExecutionResult(disposition="not_prepared")

        stage, blocked = _next_stage(candidate.events)
        if blocked == "completed":
            return BecomeI4RecoveryExecutionResult(disposition="completed")
        if blocked in {"failed", "unknown"}:
            return BecomeI4RecoveryExecutionResult(
                disposition=blocked,
                reconciliation_required=blocked == "unknown",
            )
        if stage is None:
            raise ValueError("become_i4_recovery_stage_plan_invalid")

        existing_reservation = _stage_event(
            candidate.events,
            stage=stage,
            event_sequence=1,
        )
        if existing_reservation is not None:
            await self._append_unknown_after_orphan_reservation(
                candidate=candidate,
                reservation=existing_reservation,
                now=now,
            )
            return BecomeI4RecoveryExecutionResult(
                disposition="unknown",
                stage=stage,
                attempted=False,
                reconciliation_required=True,
            )

        post_count = sum(
            item.event_type == "reserved" for item in candidate.events
        )
        if post_count >= self._max_posts:
            return BecomeI4RecoveryExecutionResult(disposition="bounded_complete")

        request, mapping_digest = _build_request(candidate, stage=stage)
        request_digest = canonical_digest(request)
        if (
            stage is BecomeI4Stage.MAYAKI
            and request_digest != candidate.attempt.original_request_digest
        ):
            raise ValueError("become_i4_recovery_failed_request_digest_mismatch")

        target = self._targets.for_stage(stage)
        if target.source_sha != _attempt_source(candidate.attempt, stage):
            raise ValueError("become_i4_recovery_source_sha_mismatch")
        reservation = await self._reserve(
            candidate=candidate,
            stage=stage,
            request=request,
            request_digest=request_digest,
            source_sha=target.source_sha,
            now=now,
        )
        if reservation is None:
            return BecomeI4RecoveryExecutionResult(
                disposition="duplicate_suppressed",
                stage=stage,
                attempted=False,
                reconciliation_required=True,
            )

        outcome = await self._transport.execute(
            target=target,
            reservation=ReservedBecomeI4Stage(
                stage=stage,
                request_digest=request_digest,
                request_payload=request,
                duplicate=False,
            ),
        )
        if outcome.receipt_payload is None:
            raise ValueError("become_i4_recovery_terminal_receipt_required")
        await self._terminal(
            candidate=candidate,
            stage=stage,
            request=request,
            request_digest=request_digest,
            source_sha=target.source_sha,
            receipt=outcome.receipt_payload,
            event_type={
                BecomeI4TransportState.COMPLETED: "completed",
                BecomeI4TransportState.FAILED: "failed",
                BecomeI4TransportState.UNKNOWN: "unknown",
                BecomeI4TransportState.RECONCILIATION_REQUIRED: "unknown",
            }[outcome.state],
            now=now,
            mapping_digest=mapping_digest,
        )
        return BecomeI4RecoveryExecutionResult(
            disposition=outcome.state.value,
            stage=stage,
            attempted=outcome.attempted,
            reconciliation_required=outcome.reconciliation_required,
        )

    async def _reserve(
        self,
        *,
        candidate: _RecoveryCandidate,
        stage: BecomeI4Stage,
        request: JsonObject,
        request_digest: str,
        source_sha: str,
        now: datetime,
    ) -> BecomeI4RecoveryStageEventModel | None:
        event_payload = build_stage_event_payload(
            attempt_digest=candidate.attempt.attempt_digest,
            stage=stage,
            event_sequence=1,
            event_type="reserved",
            request_digest=request_digest,
            source_sha=source_sha,
            receipt_digest=None,
        )
        row = BecomeI4RecoveryStageEventModel(
            workspace_id=candidate.attempt.workspace_id,
            attempt_id=candidate.attempt.id,
            stage=stage.value,
            ordinal=_STAGE_ORDINAL[stage],
            event_sequence=1,
            event_type="reserved",
            request_digest=request_digest,
            request_payload=request,
            receipt_digest=None,
            receipt_payload=None,
            source_sha=source_sha,
            event_digest=canonical_digest(event_payload),
            event_payload=event_payload,
            created_at=now,
        )
        async with self._sessions() as session:
            async with session.begin():
                duplicate = await session.scalar(
                    select(func.count(BecomeI4RecoveryStageEventModel.id)).where(
                        BecomeI4RecoveryStageEventModel.attempt_id
                        == candidate.attempt.id,
                        BecomeI4RecoveryStageEventModel.stage == stage.value,
                        BecomeI4RecoveryStageEventModel.event_sequence == 1,
                    )
                )
                if int(duplicate or 0) != 0:
                    return None
                session.add(row)
        return row

    async def _terminal(
        self,
        *,
        candidate: _RecoveryCandidate,
        stage: BecomeI4Stage,
        request: JsonObject,
        request_digest: str,
        source_sha: str,
        receipt: JsonObject,
        event_type: str,
        now: datetime,
        mapping_digest: str | None,
    ) -> None:
        receipt_digest = canonical_digest(receipt)
        if event_type == "completed":
            validate_i4_receipt(
                stage=stage,
                receipt=receipt,
                request=request,
                expected_source_sha=source_sha,
            )
            if stage is BecomeI4Stage.TAKE and mapping_digest is None:
                raise ValueError("become_i4_take_mapping_digest_required")
        event_payload = build_stage_event_payload(
            attempt_digest=candidate.attempt.attempt_digest,
            stage=stage,
            event_sequence=2,
            event_type=event_type,
            request_digest=request_digest,
            source_sha=source_sha,
            receipt_digest=receipt_digest,
        )
        row = BecomeI4RecoveryStageEventModel(
            workspace_id=candidate.attempt.workspace_id,
            attempt_id=candidate.attempt.id,
            stage=stage.value,
            ordinal=_STAGE_ORDINAL[stage],
            event_sequence=2,
            event_type=event_type,
            request_digest=request_digest,
            request_payload=request,
            receipt_digest=receipt_digest,
            receipt_payload=receipt,
            source_sha=source_sha,
            event_digest=canonical_digest(event_payload),
            event_payload=event_payload,
            created_at=now,
        )
        async with self._sessions() as session:
            async with session.begin():
                session.add(row)

    async def _append_unknown_after_orphan_reservation(
        self,
        *,
        candidate: _RecoveryCandidate,
        reservation: BecomeI4RecoveryStageEventModel,
        now: datetime,
    ) -> None:
        stage = BecomeI4Stage(reservation.stage)
        existing = _stage_event(
            candidate.events,
            stage=stage,
            event_sequence=2,
        )
        if existing is not None:
            return
        receipt: JsonObject = {
            "schema": "gate.become-i4-stage-outcome@1",
            "stage": stage.value,
            "request_digest": reservation.request_digest,
            "classification": "unknown",
            "error_code": "orphan_reservation_outcome_ambiguous",
            "external_effects": 0,
        }
        await self._terminal(
            candidate=candidate,
            stage=stage,
            request=dict(reservation.request_payload),
            request_digest=reservation.request_digest,
            source_sha=reservation.source_sha,
            receipt=receipt,
            event_type="unknown",
            now=now,
            mapping_digest=None,
        )


def validate_attempt_payload(
    raw: object,
    *,
    expected_digest: str,
) -> JsonObject:
    if not isinstance(raw, dict) or frozenset(raw) != _ATTEMPT_FIELDS:
        raise ValueError("become_i4_recovery_attempt_fields_invalid")
    sources = raw.get("source_shas")
    if (
        raw.get("schema") != ATTEMPT_SCHEMA
        or raw.get("attempt_version") != 2
        or raw.get("stage_order") != ["mayaki", "oko", "take"]
        or raw.get("max_posts") != 3
        or raw.get("state") != "prepared"
        or raw.get("external_effects") != 0
        or raw.get("contract_digest") != I4_CONTRACT_DIGEST
        or sources
        != {
            "mayaki": MAYAKI_SOURCE_SHA,
            "oko": OKO_SOURCE_SHA,
            "take": TAKE_SOURCE_SHA,
        }
    ):
        raise ValueError("become_i4_recovery_attempt_binding_invalid")
    for field in (
        "prepared_recovery_digest",
        "original_failed_request_digest",
        "original_failed_receipt_digest",
        "relations_receipt_digest",
        "transcript_digest",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("become_i4_recovery_attempt_digest_invalid")
    for field in ("attempt_ref", "correlation_ref"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.startswith("synthetic://"):
            raise ValueError("become_i4_recovery_attempt_ref_invalid")
    if canonical_digest(raw) != expected_digest:
        raise ValueError("become_i4_recovery_attempt_mutated_replay")
    return cast(JsonObject, raw)


def build_stage_event_payload(
    *,
    attempt_digest: str,
    stage: BecomeI4Stage,
    event_sequence: int,
    event_type: str,
    request_digest: str,
    source_sha: str,
    receipt_digest: str | None,
) -> JsonObject:
    if stage not in RECOVERY_STAGES:
        raise ValueError("become_i4_recovery_stage_invalid")
    if (
        (event_sequence == 1 and event_type != "reserved")
        or (
            event_sequence == 2
            and event_type not in {"completed", "failed", "unknown"}
        )
        or event_sequence not in {1, 2}
    ):
        raise ValueError("become_i4_recovery_event_order_invalid")
    payload: JsonObject = {
        "schema": EVENT_SCHEMA,
        "attempt_digest": attempt_digest,
        "attempt_version": 2,
        "stage": stage.value,
        "ordinal": _STAGE_ORDINAL[stage],
        "event_sequence": event_sequence,
        "event_type": event_type,
        "request_digest": request_digest,
        "source_sha": source_sha,
        "receipt_digest": receipt_digest,
        "external_effects": 0,
    }
    return validate_stage_event_payload(payload)


def validate_stage_event_payload(raw: object) -> JsonObject:
    if not isinstance(raw, dict) or frozenset(raw) != _EVENT_FIELDS:
        raise ValueError("become_i4_recovery_event_fields_invalid")
    try:
        stage = BecomeI4Stage(cast(str, raw.get("stage")))
    except ValueError as exc:
        raise ValueError("become_i4_recovery_event_stage_invalid") from exc
    if (
        raw.get("schema") != EVENT_SCHEMA
        or raw.get("attempt_version") != 2
        or raw.get("ordinal") != _STAGE_ORDINAL.get(stage)
        or raw.get("external_effects") != 0
    ):
        raise ValueError("become_i4_recovery_event_binding_invalid")
    sequence = raw.get("event_sequence")
    event_type = raw.get("event_type")
    if (
        (sequence == 1 and event_type != "reserved")
        or (
            sequence == 2
            and event_type not in {"completed", "failed", "unknown"}
        )
        or sequence not in {1, 2}
    ):
        raise ValueError("become_i4_recovery_event_order_invalid")
    for field, size in (
        ("attempt_digest", 64),
        ("request_digest", 64),
        ("source_sha", 40),
    ):
        value = raw.get(field)
        if not isinstance(value, str) or len(value) != size:
            raise ValueError("become_i4_recovery_event_digest_invalid")
    receipt_digest = raw.get("receipt_digest")
    if (
        (sequence == 1 and receipt_digest is not None)
        or (
            sequence == 2
            and (
                not isinstance(receipt_digest, str)
                or len(receipt_digest) != 64
            )
        )
    ):
        raise ValueError("become_i4_recovery_event_receipt_invalid")
    return cast(JsonObject, raw)


async def _load_candidate(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> _RecoveryCandidate | None:
    relations = aliased(BecomeVoiceChainSlotModel)
    row = (
        await session.execute(
            select(
                BecomeI4RecoveryAttemptModel,
                BecomeI4RecoveryReceiptModel,
                BecomeVoiceChainSlotModel,
                BecomeVoiceCorrelationModel,
                BecomeMediaIntakeModel,
                BecomeControlledConsentReceiptModel,
                BecomeRelationsConsentHandoffModel,
                relations,
            )
            .join(
                Workspace,
                Workspace.id == BecomeI4RecoveryAttemptModel.workspace_id,
            )
            .join(
                BecomeI4RecoveryReceiptModel,
                BecomeI4RecoveryReceiptModel.id
                == BecomeI4RecoveryAttemptModel.recovery_receipt_id,
            )
            .join(
                BecomeVoiceChainSlotModel,
                BecomeVoiceChainSlotModel.id
                == BecomeI4RecoveryReceiptModel.failed_slot_id,
            )
            .join(
                BecomeVoiceCorrelationModel,
                BecomeVoiceCorrelationModel.id
                == BecomeI4RecoveryAttemptModel.correlation_id,
            )
            .join(
                BecomeMediaIntakeModel,
                BecomeMediaIntakeModel.id
                == BecomeVoiceCorrelationModel.media_intake_id,
            )
            .join(
                BecomeControlledConsentReceiptModel,
                BecomeControlledConsentReceiptModel.id
                == BecomeMediaIntakeModel.consent_receipt_id,
            )
            .join(
                BecomeRelationsConsentHandoffModel,
                BecomeRelationsConsentHandoffModel.id
                == BecomeMediaIntakeModel.relations_handoff_id,
            )
            .join(
                relations,
                relations.correlation_id == BecomeVoiceCorrelationModel.id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                relations.stage == "relations",
                relations.state == BecomeChainSlotState.COMPLETED.value,
            )
            .limit(2)
        )
    ).all()
    if not row:
        return None
    if len(row) != 1:
        raise ValueError("become_i4_recovery_attempt_cardinality_invalid")
    (
        attempt,
        recovery,
        failed,
        correlation,
        intake,
        consent,
        handoff,
        relations_slot,
    ) = row[0]
    payload = validate_attempt_payload(
        attempt.attempt_payload,
        expected_digest=attempt.attempt_digest,
    )
    validate_recovery_replay(
        recovery.receipt_payload,
        expected_digest=recovery.receipt_digest,
    )
    if (
        attempt.schema_name != ATTEMPT_SCHEMA
        or attempt.attempt_version != 2
        or attempt.state != "prepared"
        or attempt.max_posts != 3
        or attempt.prepared_recovery_digest != recovery.receipt_digest
        or attempt.original_request_digest != failed.request_digest
        or attempt.original_receipt_digest != failed.receipt_digest
        or attempt.relations_receipt_digest != relations_slot.receipt_digest
        or attempt.transcript_digest != intake.transcript_digest
        or attempt.contract_digest != I4_CONTRACT_DIGEST
        or attempt.mayaki_source_sha != MAYAKI_SOURCE_SHA
        or attempt.oko_source_sha != OKO_SOURCE_SHA
        or attempt.take_source_sha != TAKE_SOURCE_SHA
        or payload["correlation_ref"] != correlation.correlation_ref
        or failed.stage != "mayaki"
        or failed.state != BecomeChainSlotState.FAILED.value
        or canonical_digest(failed.request_payload) != failed.request_digest
        or canonical_digest(failed.receipt_payload) != failed.receipt_digest
        or relations_slot.receipt_payload is None
        or canonical_digest(relations_slot.receipt_payload)
        != relations_slot.receipt_digest
    ):
        raise ValueError("become_i4_recovery_attempt_record_invalid")
    failure = failed.receipt_payload
    if (
        not isinstance(failure, dict)
        or failure.get("classification") != "failed"
        or failure.get("error_code") != "connection_not_established"
        or failure.get("external_effects") != 0
    ):
        raise ValueError("become_i4_recovery_source_failure_not_preserved")
    evidence = BecomeI4CorrelationEvidence(
        correlation_ref=correlation.correlation_ref,
        consent_quest_ref=consent.quest_ref,
        gate_subject_ref=intake.subject_ref,
        voice_intake_digest=correlation.intake_digest,
        transcript_digest=_required(intake.transcript_digest),
        consent_receipt_digest=consent.receipt_digest,
        relations_execution_digest=_required(handoff.execution_digest),
        relations_domain_event_id=_required(handoff.domain_event_id),
        relations_domain_event_digest=_required(handoff.domain_event_digest),
    )
    events = tuple(
        (
            await session.scalars(
                select(BecomeI4RecoveryStageEventModel)
                .where(
                    BecomeI4RecoveryStageEventModel.attempt_id == attempt.id,
                )
                .order_by(
                    BecomeI4RecoveryStageEventModel.ordinal,
                    BecomeI4RecoveryStageEventModel.event_sequence,
                )
            )
        ).all()
    )
    _validate_events(attempt=attempt, events=events)
    return _RecoveryCandidate(
        attempt=attempt,
        evidence=evidence,
        failed_slot=failed,
        relations_slot=relations_slot,
        events=events,
    )


def _validate_events(
    *,
    attempt: BecomeI4RecoveryAttemptModel,
    events: tuple[BecomeI4RecoveryStageEventModel, ...],
) -> None:
    validate_recovery_event_sequence(
        [
            (event.stage, event.event_sequence, event.event_type)
            for event in events
        ]
    )
    seen: set[tuple[str, int]] = set()
    for event in events:
        identity = (event.stage, event.event_sequence)
        if identity in seen:
            raise ValueError("become_i4_recovery_duplicate_event")
        seen.add(identity)
        payload = validate_stage_event_payload(event.event_payload)
        if (
            event.attempt_id != attempt.id
            or event.workspace_id != attempt.workspace_id
            or event.event_digest != canonical_digest(payload)
            or payload["attempt_digest"] != attempt.attempt_digest
            or payload["request_digest"] != event.request_digest
            or payload["source_sha"] != event.source_sha
            or payload["receipt_digest"] != event.receipt_digest
            or canonical_digest(event.request_payload) != event.request_digest
            or (
                event.receipt_payload is not None
                and canonical_digest(event.receipt_payload)
                != event.receipt_digest
            )
        ):
            raise ValueError("become_i4_recovery_event_record_invalid")


def _next_stage(
    events: tuple[BecomeI4RecoveryStageEventModel, ...],
) -> tuple[BecomeI4Stage | None, str | None]:
    for stage in RECOVERY_STAGES:
        terminal = _stage_event(events, stage=stage, event_sequence=2)
        if terminal is not None:
            if terminal.event_type == "completed":
                continue
            return None, terminal.event_type
        return stage, None
    return None, "completed"


def _stage_event(
    events: tuple[BecomeI4RecoveryStageEventModel, ...],
    *,
    stage: BecomeI4Stage,
    event_sequence: int,
) -> BecomeI4RecoveryStageEventModel | None:
    return next(
        (
            item
            for item in events
            if item.stage == stage.value
            and item.event_sequence == event_sequence
        ),
        None,
    )


def _build_request(
    candidate: _RecoveryCandidate,
    *,
    stage: BecomeI4Stage,
) -> tuple[JsonObject, str | None]:
    refs = project_i4_refs(candidate.evidence)
    relations = _payload(candidate.relations_slot.receipt_payload)
    if stage is BecomeI4Stage.MAYAKI:
        return build_mayaki_request(candidate.evidence, refs, relations), None
    mayaki = _completed_receipt(candidate.events, BecomeI4Stage.MAYAKI)
    if stage is BecomeI4Stage.OKO:
        return (
            build_oko_request(
                candidate.evidence,
                refs,
                relations,
                mayaki,
            ),
            None,
        )
    oko = _completed_receipt(candidate.events, BecomeI4Stage.OKO)
    take = build_take_request(
        candidate.evidence,
        refs,
        relations,
        mayaki,
        oko,
    )
    return take.request, take.mapping_digest


def _completed_receipt(
    events: tuple[BecomeI4RecoveryStageEventModel, ...],
    stage: BecomeI4Stage,
) -> JsonObject:
    event = _stage_event(events, stage=stage, event_sequence=2)
    if event is None or event.event_type != "completed":
        raise ValueError(f"become_i4_recovery_{stage.value}_completion_required")
    return _payload(event.receipt_payload)


def _attempt_source(
    attempt: BecomeI4RecoveryAttemptModel,
    stage: BecomeI4Stage,
) -> str:
    return {
        BecomeI4Stage.MAYAKI: attempt.mayaki_source_sha,
        BecomeI4Stage.OKO: attempt.oko_source_sha,
        BecomeI4Stage.TAKE: attempt.take_source_sha,
    }[stage]


def _payload(value: dict[str, object] | None) -> JsonObject:
    if value is None:
        raise ValueError("become_i4_recovery_receipt_missing")
    return dict(value)


def _required(value: str | None) -> str:
    if value is None:
        raise ValueError("become_i4_recovery_evidence_incomplete")
    return value
