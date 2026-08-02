"""Default-off durable executor for the private BECOME I4 chain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_i4_chain import (
    BecomeI4CorrelationEvidence,
    BecomeI4Stage,
    JsonObject,
    build_mayaki_request,
    build_oko_request,
    build_relations_request,
    build_take_request,
    project_i4_refs,
)
from app.application.become_voice_chain import (
    BecomeChainSlotState,
    BecomeChainStage,
    FinalizeBecomeChainStageService,
    ReserveBecomeChainStageService,
)
from app.infrastructure.become_i4_chain import (
    BecomeI4HttpClient,
    BecomeI4ProductionTargets,
    BecomeI4TransportState,
    PostReservedBecomeI4StageOnce,
    ReservedBecomeI4Stage,
)
from app.infrastructure.become_voice_chain import (
    SessionFactoryBecomeVoiceChainStore,
)
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeMediaIntakeModel,
    BecomeRelationsConsentHandoffModel,
    BecomeVoiceChainSlotModel,
    BecomeVoiceCorrelationModel,
)
from app.models.canonical import Workspace

_MAX_CHAIN_ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class BecomeI4ExecutionResult:
    """Content-free outcome of one worker pass."""

    disposition: str
    stage: BecomeI4Stage | None = None
    attempted: bool = False
    reconciliation_required: bool = False


@dataclass(frozen=True, slots=True)
class _ReadyStage:
    evidence: BecomeI4CorrelationEvidence
    stage: BecomeI4Stage
    prior_receipts: dict[BecomeI4Stage, JsonObject]


class SessionFactoryBecomeI4Executor:
    """Reserve, post and finalize at most one exact stage per pass."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        http_client: BecomeI4HttpClient,
        enabled: bool,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._sessions = sessions
        self._enabled = enabled
        self._targets = BecomeI4ProductionTargets()
        self._transport = PostReservedBecomeI4StageOnce(
            http_client,
            timeout_seconds=timeout_seconds,
        )
        store = SessionFactoryBecomeVoiceChainStore(sessions)
        self._reserve = ReserveBecomeChainStageService(store)
        self._finalize = FinalizeBecomeChainStageService(store)

    async def run_once(
        self,
        *,
        workspace_key: str,
        now: datetime,
    ) -> BecomeI4ExecutionResult:
        if not self._enabled:
            return BecomeI4ExecutionResult(disposition="disabled")
        if now.tzinfo is None:
            raise ValueError("become_i4_execution_timestamp_invalid")

        async with self._sessions() as session:
            requested_or_unknown = int(
                (
                    await session.scalar(
                        select(func.count(BecomeVoiceChainSlotModel.id))
                        .join(
                            Workspace,
                            Workspace.id == BecomeVoiceChainSlotModel.workspace_id,
                        )
                        .where(
                            Workspace.key == workspace_key,
                            Workspace.is_active.is_(True),
                            BecomeVoiceChainSlotModel.state.in_(
                                (
                                    BecomeChainSlotState.REQUESTED.value,
                                    BecomeChainSlotState.UNKNOWN.value,
                                )
                            ),
                        )
                    )
                )
                or 0
            )
            if requested_or_unknown:
                return BecomeI4ExecutionResult(
                    disposition="reconciliation_required",
                    reconciliation_required=True,
                )
            attempted = int(
                (
                    await session.scalar(
                        select(func.count(BecomeVoiceChainSlotModel.id))
                        .join(
                            Workspace,
                            Workspace.id == BecomeVoiceChainSlotModel.workspace_id,
                        )
                        .where(
                            Workspace.key == workspace_key,
                            Workspace.is_active.is_(True),
                            BecomeVoiceChainSlotModel.state.in_(
                                (
                                    BecomeChainSlotState.REQUESTED.value,
                                    BecomeChainSlotState.COMPLETED.value,
                                    BecomeChainSlotState.FAILED.value,
                                    BecomeChainSlotState.UNKNOWN.value,
                                )
                            ),
                        )
                    )
                )
                or 0
            )
            if attempted >= _MAX_CHAIN_ATTEMPTS:
                return BecomeI4ExecutionResult(disposition="bounded_complete")
            candidate = await _load_ready_stage(
                session,
                workspace_key=workspace_key,
            )
        if candidate is None:
            return BecomeI4ExecutionResult(disposition="idle")

        request, mapping_digest = _build_request(candidate)
        target = self._targets.for_stage(candidate.stage)
        reserved = await self._reserve.execute(
            workspace_key=workspace_key,
            correlation_ref=candidate.evidence.correlation_ref,
            stage=BecomeChainStage(candidate.stage.value),
            request_payload=request,
            source_sha=target.source_sha,
            requested_at=now,
            mapping_digest=mapping_digest,
        )
        if reserved.duplicate:
            if reserved.state is BecomeChainSlotState.COMPLETED:
                return BecomeI4ExecutionResult(
                    disposition="duplicate_completed",
                    stage=candidate.stage,
                )
            return BecomeI4ExecutionResult(
                disposition="reconciliation_required",
                stage=candidate.stage,
                reconciliation_required=True,
            )

        outcome = await self._transport.execute(
            target=target,
            reservation=ReservedBecomeI4Stage(
                stage=candidate.stage,
                request_digest=reserved.request_digest,
                request_payload=request,
                duplicate=False,
            ),
        )
        if outcome.receipt_payload is None:
            return BecomeI4ExecutionResult(
                disposition="reconciliation_required",
                stage=candidate.stage,
                attempted=outcome.attempted,
                reconciliation_required=True,
            )
        terminal_state = {
            BecomeI4TransportState.COMPLETED: BecomeChainSlotState.COMPLETED,
            BecomeI4TransportState.FAILED: BecomeChainSlotState.FAILED,
            BecomeI4TransportState.UNKNOWN: BecomeChainSlotState.UNKNOWN,
            BecomeI4TransportState.RECONCILIATION_REQUIRED: (BecomeChainSlotState.UNKNOWN),
        }[outcome.state]
        await self._finalize.execute(
            workspace_key=workspace_key,
            correlation_ref=candidate.evidence.correlation_ref,
            stage=BecomeChainStage(candidate.stage.value),
            request_digest=outcome.request_digest,
            receipt_payload=outcome.receipt_payload,
            state=terminal_state,
            finished_at=now,
        )
        return BecomeI4ExecutionResult(
            disposition=outcome.state.value,
            stage=candidate.stage,
            attempted=outcome.attempted,
            reconciliation_required=outcome.reconciliation_required,
        )


async def _load_ready_stage(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> _ReadyStage | None:
    row = (
        await session.execute(
            select(
                BecomeVoiceCorrelationModel,
                BecomeMediaIntakeModel,
                BecomeControlledConsentReceiptModel,
                BecomeRelationsConsentHandoffModel,
                BecomeVoiceChainSlotModel,
            )
            .join(
                Workspace,
                Workspace.id == BecomeVoiceCorrelationModel.workspace_id,
            )
            .join(
                BecomeMediaIntakeModel,
                BecomeMediaIntakeModel.id == BecomeVoiceCorrelationModel.media_intake_id,
            )
            .join(
                BecomeControlledConsentReceiptModel,
                BecomeControlledConsentReceiptModel.id == BecomeMediaIntakeModel.consent_receipt_id,
            )
            .join(
                BecomeRelationsConsentHandoffModel,
                BecomeRelationsConsentHandoffModel.id
                == BecomeMediaIntakeModel.relations_handoff_id,
            )
            .join(
                BecomeVoiceChainSlotModel,
                BecomeVoiceChainSlotModel.correlation_id == BecomeVoiceCorrelationModel.id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                BecomeMediaIntakeModel.transcript_state == "confirmed",
                BecomeRelationsConsentHandoffModel.status == "executed",
                BecomeVoiceChainSlotModel.state == BecomeChainSlotState.READY.value,
            )
            .order_by(
                BecomeVoiceCorrelationModel.created_at,
                BecomeVoiceChainSlotModel.position,
            )
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return None
    correlation, intake, consent, handoff, slot = row
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
    prior = (
        await session.scalars(
            select(BecomeVoiceChainSlotModel)
            .where(
                BecomeVoiceChainSlotModel.correlation_id == correlation.id,
                BecomeVoiceChainSlotModel.position < slot.position,
                BecomeVoiceChainSlotModel.state == BecomeChainSlotState.COMPLETED.value,
            )
            .order_by(BecomeVoiceChainSlotModel.position)
        )
    ).all()
    prior_receipts = {
        BecomeI4Stage(item.stage): _required_payload(item.receipt_payload) for item in prior
    }
    if len(prior_receipts) != slot.position - 1:
        raise ValueError("become_i4_prior_receipt_chain_invalid")
    return _ReadyStage(
        evidence=evidence,
        stage=BecomeI4Stage(slot.stage),
        prior_receipts=prior_receipts,
    )


def _build_request(candidate: _ReadyStage) -> tuple[JsonObject, str | None]:
    refs = project_i4_refs(candidate.evidence)
    receipts = candidate.prior_receipts
    if candidate.stage is BecomeI4Stage.RELATIONS:
        return build_relations_request(candidate.evidence, refs), None
    relations = receipts[BecomeI4Stage.RELATIONS]
    if candidate.stage is BecomeI4Stage.MAYAKI:
        return build_mayaki_request(candidate.evidence, refs, relations), None
    mayaki = receipts[BecomeI4Stage.MAYAKI]
    if candidate.stage is BecomeI4Stage.OKO:
        return (
            build_oko_request(candidate.evidence, refs, relations, mayaki),
            None,
        )
    oko = receipts[BecomeI4Stage.OKO]
    take = build_take_request(
        candidate.evidence,
        refs,
        relations,
        mayaki,
        oko,
    )
    return take.request, take.mapping_digest


def _required(value: str | None) -> str:
    if value is None:
        raise ValueError("become_i4_evidence_incomplete")
    return value


def _required_payload(value: dict[str, object] | None) -> JsonObject:
    if value is None:
        raise ValueError("become_i4_prior_receipt_missing")
    return dict(value)
