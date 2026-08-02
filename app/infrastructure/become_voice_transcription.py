"""PostgreSQL store and redacted status for BECOME voice transcription."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)
from app.application.become_media_intake import CONFIRMED_TRANSCRIPT_RETENTION
from app.application.become_voice_chain import BecomeChainSlotState
from app.application.become_voice_transcription import (
    BECOME_TRANSCRIPTION_GRANT_KEY,
    BECOME_TRANSCRIPTION_SCHEMA,
    BECOME_TRANSCRIPTION_SCOPE_KIND,
    BECOME_TRANSCRIPTION_SCOPE_REF,
    BecomeVoiceTranscriptionState,
    BecomeVoiceTranscriptionWork,
)
from app.application.billing import BillingProduct, BudgetReservationState
from app.application.telegram_voice_transcription import ProviderTranscript
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
    BecomeMediaIntakeModel,
    BecomeRelationsConsentHandoffModel,
    BecomeVoiceChainSlotModel,
    BecomeVoiceCorrelationModel,
    BecomeVoiceTranscriptionAttemptModel,
)
from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
)
from app.models.canonical import TransportConnector, Workspace


class SessionFactoryBecomeVoiceTranscriptionStore:
    """Claim one exact archived voice and preserve every provider boundary."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim_or_resume(
        self,
        *,
        workspace_key: str,
        now: datetime,
        model: str,
        language: str,
        reserve_microusd_per_second: int,
        stale_after: timedelta,
    ) -> BecomeVoiceTranscriptionWork | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("become_transcription_time_invalid")
        async with self._sessions.begin() as session:
            workspace = await session.scalar(
                select(Workspace)
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
                .with_for_update()
            )
            if workspace is None:
                raise BecomeControlledIntakeError(
                    "become_transcription_workspace_not_active"
                )
            existing = await session.scalar(
                select(BecomeVoiceTranscriptionAttemptModel)
                .where(
                    BecomeVoiceTranscriptionAttemptModel.workspace_id
                    == workspace.id
                )
                .with_for_update()
            )
            if existing is not None:
                state = BecomeVoiceTranscriptionState(existing.state)
                if state is BecomeVoiceTranscriptionState.CALLING:
                    if existing.updated_at > now - stale_after:
                        return None
                    return await _work(session, existing, workspace_key)
                if state in {
                    BecomeVoiceTranscriptionState.RESERVING,
                    BecomeVoiceTranscriptionState.RESERVED,
                    BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED,
                }:
                    return await _work(session, existing, workspace_key)
                return None

            candidates = (
                await session.execute(
                    select(
                        BecomeVoiceCorrelationModel,
                        BecomeMediaIntakeModel,
                    )
                    .join(
                        BecomeMediaIntakeModel,
                        BecomeMediaIntakeModel.id
                        == BecomeVoiceCorrelationModel.media_intake_id,
                    )
                    .join(
                        AgentInstallationModel,
                        (
                            AgentInstallationModel.id
                            == BecomeMediaIntakeModel.installation_id
                        )
                        & (
                            AgentInstallationModel.workspace_id
                            == BecomeMediaIntakeModel.workspace_id
                        ),
                    )
                    .join(
                        TransportConnector,
                        (
                            TransportConnector.id
                            == AgentInstallationModel.connector_id
                        )
                        & (
                            TransportConnector.workspace_id
                            == AgentInstallationModel.workspace_id
                        ),
                    )
                    .join(
                        BecomeControlledIntakeReceiptModel,
                        BecomeControlledIntakeReceiptModel.id
                        == BecomeMediaIntakeModel.registration_receipt_id,
                    )
                    .join(
                        BecomeControlledConsentReceiptModel,
                        BecomeControlledConsentReceiptModel.id
                        == BecomeMediaIntakeModel.consent_receipt_id,
                    )
                    .join(
                        BecomeRelationsConsentHandoffModel,
                        (
                            BecomeRelationsConsentHandoffModel.id
                            == BecomeMediaIntakeModel.relations_handoff_id
                        )
                        & (
                            BecomeRelationsConsentHandoffModel.consent_receipt_id
                            == BecomeControlledConsentReceiptModel.id
                        ),
                    )
                    .where(
                        BecomeVoiceCorrelationModel.workspace_id
                        == workspace.id,
                        BecomeVoiceCorrelationModel.media_intake_id
                        == BecomeMediaIntakeModel.id,
                        BecomeMediaIntakeModel.workspace_id == workspace.id,
                        AgentInstallationModel.state == "active",
                        TransportConnector.connector_key
                        == BECOME_TRANSCRIPTION_SCOPE_REF,
                        TransportConnector.is_enabled.is_(True),
                        BecomeControlledIntakeReceiptModel.workspace_id
                        == workspace.id,
                        BecomeControlledIntakeReceiptModel.installation_id
                        == AgentInstallationModel.id,
                        BecomeControlledIntakeReceiptModel.intent
                        == "registration_confirmed",
                        BecomeControlledIntakeReceiptModel.group_state
                        == "owner_only",
                        BecomeControlledIntakeReceiptModel.member_count == 2,
                        BecomeControlledConsentReceiptModel.workspace_id
                        == workspace.id,
                        BecomeControlledConsentReceiptModel.installation_id
                        == AgentInstallationModel.id,
                        BecomeControlledConsentReceiptModel.intent
                        == "consent_accepted",
                        BecomeControlledConsentReceiptModel.group_state
                        == "owner_only",
                        BecomeControlledConsentReceiptModel.member_count == 2,
                        BecomeMediaIntakeModel.kind == "voice",
                        BecomeMediaIntakeModel.status == "archived",
                        BecomeMediaIntakeModel.raw_data.is_not(None),
                        BecomeMediaIntakeModel.raw_expires_at > now,
                        BecomeMediaIntakeModel.transcript_state == "none",
                        BecomeRelationsConsentHandoffModel.status == "executed",
                        BecomeRelationsConsentHandoffModel.execution_digest
                        .is_not(None),
                        BecomeRelationsConsentHandoffModel.executed_at
                        .is_not(None),
                    )
                    .order_by(BecomeMediaIntakeModel.received_at)
                    .limit(2)
                    .with_for_update(
                        of=(
                            BecomeVoiceCorrelationModel,
                            BecomeMediaIntakeModel,
                            AgentInstallationModel,
                            TransportConnector,
                            BecomeControlledIntakeReceiptModel,
                            BecomeControlledConsentReceiptModel,
                            BecomeRelationsConsentHandoffModel,
                        )
                    )
                )
            ).all()
            if not candidates:
                return None
            if len(candidates) != 1:
                raise BecomeControlledIntakeError(
                    "become_transcription_voice_scope_ambiguous"
                )
            correlation, intake = candidates[0]
            if (
                intake.duration_seconds <= 0
                or intake.duration_seconds > 600
                or intake.raw_data is None
                or intake.raw_media_type is None
            ):
                raise BecomeControlledIntakeError(
                    "become_transcription_voice_not_eligible"
                )
            requested = (
                intake.duration_seconds * reserve_microusd_per_second
            )
            if requested > 100_000:
                raise BecomeControlledIntakeError(
                    "become_transcription_reservation_exceeds_grant"
                )
            attempt = BecomeVoiceTranscriptionAttemptModel(
                id=uuid.uuid4(),
                workspace_id=workspace.id,
                correlation_id=correlation.id,
                media_intake_id=intake.id,
                grant_key=BECOME_TRANSCRIPTION_GRANT_KEY,
                schema_name=BECOME_TRANSCRIPTION_SCHEMA,
                state=BecomeVoiceTranscriptionState.RESERVING.value,
                reservation_key=(
                    "become-transcription:" + intake.intake_key
                )[:128],
                period_key=now.strftime("%Y-%m"),
                requested_microusd=requested,
                model=model,
                language=language,
                provider_call_started_at=None,
                provider_operation_ref=None,
                staged_transcript=None,
                transcript_digest=None,
                provider_succeeded_at=None,
                billed_quantity=None,
                actual_microusd=None,
                reason_code=None,
                receipt_digest=None,
                created_at=now,
                updated_at=now,
            )
            session.add(attempt)
            await session.flush()
            return _make_work(
                attempt,
                workspace_key=workspace_key,
                correlation_ref=correlation.correlation_ref,
                intake=intake,
            )

    async def mark_blocked(
        self,
        *,
        attempt_id: str,
        reason_code: str,
        now: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            attempt = await _attempt(session, attempt_id)
            if attempt.state not in {
                BecomeVoiceTranscriptionState.RESERVING.value,
                BecomeVoiceTranscriptionState.RESERVED.value,
            }:
                raise BecomeControlledIntakeError(
                    "become_transcription_block_transition_invalid"
                )
            attempt.state = BecomeVoiceTranscriptionState.BLOCKED.value
            attempt.reason_code = reason_code
            attempt.updated_at = now

    async def mark_reserved(
        self,
        *,
        attempt_id: str,
        now: datetime,
    ) -> BecomeVoiceTranscriptionWork:
        async with self._sessions.begin() as session:
            attempt = await _attempt(session, attempt_id)
            if attempt.state == BecomeVoiceTranscriptionState.RESERVING.value:
                attempt.state = BecomeVoiceTranscriptionState.RESERVED.value
                attempt.updated_at = now
            elif attempt.state != BecomeVoiceTranscriptionState.RESERVED.value:
                raise BecomeControlledIntakeError(
                    "become_transcription_reserve_transition_invalid"
                )
            workspace_key = await _workspace_key(session, attempt.workspace_id)
            return await _work(session, attempt, workspace_key)

    async def begin_call(
        self,
        *,
        attempt_id: str,
        now: datetime,
    ) -> BecomeVoiceTranscriptionWork | None:
        async with self._sessions.begin() as session:
            attempt = await _attempt(session, attempt_id)
            if attempt.state != BecomeVoiceTranscriptionState.RESERVED.value:
                return None
            attempt.state = BecomeVoiceTranscriptionState.CALLING.value
            attempt.provider_call_started_at = now
            attempt.updated_at = now
            workspace_key = await _workspace_key(session, attempt.workspace_id)
            await session.flush()
            return await _work(session, attempt, workspace_key)

    async def persist_success(
        self,
        *,
        attempt_id: str,
        transcript: ProviderTranscript,
        confirmed_at: datetime,
    ) -> BecomeVoiceTranscriptionWork:
        normalized = transcript.text.strip()
        if (
            not normalized
            or normalized != transcript.text
            or transcript.actual_microusd < 0
            or transcript.billed_quantity < 0
        ):
            raise BecomeControlledIntakeError(
                "become_transcription_success_invalid"
            )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        async with self._sessions.begin() as session:
            attempt = await _attempt(session, attempt_id)
            if attempt.state != BecomeVoiceTranscriptionState.CALLING.value:
                raise BecomeControlledIntakeError(
                    "become_transcription_success_transition_invalid"
                )
            if transcript.actual_microusd > attempt.requested_microusd:
                raise BecomeControlledIntakeError(
                    "become_transcription_settlement_exceeds_reservation"
                )
            correlation = await session.scalar(
                select(BecomeVoiceCorrelationModel)
                .where(
                    BecomeVoiceCorrelationModel.id
                    == attempt.correlation_id
                )
                .with_for_update()
            )
            intake = await session.scalar(
                select(BecomeMediaIntakeModel)
                .where(
                    BecomeMediaIntakeModel.id == attempt.media_intake_id
                )
                .with_for_update()
            )
            if (
                correlation is None
                or intake is None
                or correlation.media_intake_id != intake.id
                or correlation.workspace_id != attempt.workspace_id
                or intake.workspace_id != attempt.workspace_id
                or intake.status != "archived"
                or intake.raw_data is None
                or intake.raw_expires_at <= confirmed_at
                or intake.transcript_state != "none"
            ):
                raise BecomeControlledIntakeError(
                    "become_transcription_binding_invalid"
                )
            expires_at = confirmed_at + CONFIRMED_TRANSCRIPT_RETENTION
            receipt_payload = {
                "schema": BECOME_TRANSCRIPTION_SCHEMA,
                "correlation_ref": correlation.correlation_ref,
                "grant_key": attempt.grant_key,
                "model": attempt.model,
                "transcript_digest": digest,
                "transcript_expires_at": expires_at.isoformat(),
                "billed_quantity": transcript.billed_quantity,
                "actual_microusd": transcript.actual_microusd,
            }
            attempt.state = (
                BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED.value
            )
            attempt.provider_operation_ref = (
                transcript.provider_operation_ref
            )
            attempt.staged_transcript = normalized
            attempt.transcript_digest = digest
            attempt.provider_succeeded_at = confirmed_at
            attempt.billed_quantity = transcript.billed_quantity
            attempt.actual_microusd = transcript.actual_microusd
            attempt.reason_code = None
            attempt.receipt_digest = hashlib.sha256(
                json.dumps(
                    receipt_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            attempt.updated_at = confirmed_at
            workspace_key = await _workspace_key(
                session,
                attempt.workspace_id,
            )
            await session.flush()
            return _make_work(
                attempt,
                workspace_key=workspace_key,
                correlation_ref=correlation.correlation_ref,
                intake=intake,
            )

    async def confirm_success(
        self,
        *,
        attempt_id: str,
        now: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            attempt = await _attempt(session, attempt_id)
            if attempt.state == BecomeVoiceTranscriptionState.CONFIRMED.value:
                return
            if (
                attempt.state
                != BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED.value
                or attempt.staged_transcript is None
                or attempt.transcript_digest is None
                or attempt.provider_succeeded_at is None
            ):
                raise BecomeControlledIntakeError(
                    "become_transcription_confirmation_not_ready"
                )
            intake = await session.scalar(
                select(BecomeMediaIntakeModel)
                .where(
                    BecomeMediaIntakeModel.id == attempt.media_intake_id
                )
                .with_for_update()
            )
            correlation = await session.scalar(
                select(BecomeVoiceCorrelationModel)
                .where(
                    BecomeVoiceCorrelationModel.id
                    == attempt.correlation_id
                )
                .with_for_update()
            )
            if (
                intake is None
                or correlation is None
                or correlation.media_intake_id != intake.id
                or correlation.workspace_id != attempt.workspace_id
                or intake.workspace_id != attempt.workspace_id
                or intake.status not in {"archived", "raw_purged"}
                or intake.transcript_state != "none"
            ):
                raise BecomeControlledIntakeError(
                    "become_transcription_confirmation_binding_invalid"
                )
            confirmed_at = attempt.provider_succeeded_at
            expires_at = confirmed_at + CONFIRMED_TRANSCRIPT_RETENTION
            intake.transcript_state = "confirmed"
            intake.confirmed_transcript = attempt.staged_transcript
            intake.transcript_digest = attempt.transcript_digest
            intake.transcript_confirmed_at = confirmed_at
            intake.transcript_expires_at = expires_at
            intake.updated_at = now
            relations = await session.scalar(
                select(BecomeVoiceChainSlotModel)
                .where(
                    BecomeVoiceChainSlotModel.correlation_id
                    == correlation.id,
                    BecomeVoiceChainSlotModel.stage == "relations",
                    BecomeVoiceChainSlotModel.position == 1,
                )
                .with_for_update()
            )
            if (
                relations is None
                or relations.state
                not in {
                    BecomeChainSlotState.BLOCKED.value,
                    BecomeChainSlotState.READY.value,
                }
            ):
                raise BecomeControlledIntakeError(
                    "become_transcription_relations_slot_invalid"
                )
            relations.state = BecomeChainSlotState.READY.value
            relations.updated_at = now
            attempt.state = BecomeVoiceTranscriptionState.CONFIRMED.value
            attempt.staged_transcript = None
            attempt.reason_code = None
            attempt.updated_at = now

    async def mark_terminal(
        self,
        *,
        attempt_id: str,
        state: BecomeVoiceTranscriptionState,
        reason_code: str | None,
        now: datetime,
    ) -> None:
        if state not in {
            BecomeVoiceTranscriptionState.REJECTED,
            BecomeVoiceTranscriptionState.UNKNOWN,
        }:
            raise ValueError("become_transcription_terminal_state_invalid")
        async with self._sessions.begin() as session:
            attempt = await _attempt(session, attempt_id)
            current = BecomeVoiceTranscriptionState(attempt.state)
            allowed = {
                BecomeVoiceTranscriptionState.RESERVING: {
                    BecomeVoiceTranscriptionState.UNKNOWN,
                },
                BecomeVoiceTranscriptionState.RESERVED: {
                    BecomeVoiceTranscriptionState.UNKNOWN,
                },
                BecomeVoiceTranscriptionState.CALLING: {
                    BecomeVoiceTranscriptionState.REJECTED,
                    BecomeVoiceTranscriptionState.UNKNOWN,
                },
                BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED: {
                    BecomeVoiceTranscriptionState.UNKNOWN,
                },
            }
            if state not in allowed.get(current, set()):
                if current is state:
                    return
                raise BecomeControlledIntakeError(
                    "become_transcription_terminal_transition_invalid"
                )
            attempt.state = state.value
            attempt.reason_code = reason_code
            attempt.updated_at = now


async def become_voice_transcription_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    enabled: bool,
    data_gate_approved: bool,
    max_calls: int,
    api_key_configured: bool,
    now: datetime,
) -> dict[str, object]:
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None:
        raise ValueError("become_transcription_status_workspace_not_active")
    attempts = (
        await session.execute(
            select(
                func.count(BecomeVoiceTranscriptionAttemptModel.id),
                func.count(BecomeVoiceTranscriptionAttemptModel.id).filter(
                    BecomeVoiceTranscriptionAttemptModel.state
                    == BecomeVoiceTranscriptionState.CALLING.value
                ),
                func.count(BecomeVoiceTranscriptionAttemptModel.id).filter(
                    BecomeVoiceTranscriptionAttemptModel.state
                    == BecomeVoiceTranscriptionState.SUCCEEDED_UNSETTLED.value
                ),
                func.count(BecomeVoiceTranscriptionAttemptModel.id).filter(
                    BecomeVoiceTranscriptionAttemptModel.state
                    == BecomeVoiceTranscriptionState.CONFIRMED.value
                ),
                func.count(BecomeVoiceTranscriptionAttemptModel.id).filter(
                    BecomeVoiceTranscriptionAttemptModel.state
                    == BecomeVoiceTranscriptionState.REJECTED.value
                ),
                func.count(BecomeVoiceTranscriptionAttemptModel.id).filter(
                    BecomeVoiceTranscriptionAttemptModel.state
                    == BecomeVoiceTranscriptionState.UNKNOWN.value
                ),
                func.count(BecomeVoiceTranscriptionAttemptModel.id).filter(
                    BecomeVoiceTranscriptionAttemptModel.state
                    == BecomeVoiceTranscriptionState.BLOCKED.value
                ),
            ).where(
                BecomeVoiceTranscriptionAttemptModel.workspace_id
                == workspace.id
            )
        )
    ).one()
    entitlement_count = int(
        (
            await session.scalar(
                select(func.count(BillingEntitlementModel.id)).where(
                    BillingEntitlementModel.workspace_id == workspace.id,
                    BillingEntitlementModel.product
                    == BillingProduct.GATE.value,
                    BillingEntitlementModel.scope_kind
                    == BECOME_TRANSCRIPTION_SCOPE_KIND,
                    BillingEntitlementModel.scope_ref
                    == BECOME_TRANSCRIPTION_SCOPE_REF,
                    BillingEntitlementModel.state == "active",
                    BillingEntitlementModel.valid_from <= now,
                    or_(
                        BillingEntitlementModel.valid_until.is_(None),
                        BillingEntitlementModel.valid_until > now,
                    ),
                    BillingEntitlementModel.hard_limit_microusd.is_not(None),
                )
            )
        )
        or 0
    )
    billing = (
        await session.execute(
            select(
                func.count(BillingBudgetReservationModel.id).filter(
                    BillingBudgetReservationModel.state
                    == BudgetReservationState.RESERVED.value
                ),
                func.count(BillingBudgetReservationModel.id).filter(
                    BillingBudgetReservationModel.state
                    == BudgetReservationState.UNKNOWN.value
                ),
            ).where(
                BillingBudgetReservationModel.workspace_id == workspace.id,
                BillingBudgetReservationModel.product
                == BillingProduct.GATE.value,
                BillingBudgetReservationModel.scope_kind
                == BECOME_TRANSCRIPTION_SCOPE_KIND,
                BillingBudgetReservationModel.scope_ref
                == BECOME_TRANSCRIPTION_SCOPE_REF,
            )
        )
    ).one()
    total = int(attempts[0] or 0)
    calling = int(attempts[1] or 0)
    unsettled = int(attempts[2] or 0)
    confirmed = int(attempts[3] or 0)
    rejected = int(attempts[4] or 0)
    unknown = int(attempts[5] or 0)
    blocked = int(attempts[6] or 0)
    open_budget = int(billing[0] or 0)
    unknown_budget = int(billing[1] or 0)
    call_boundary = calling + unsettled + confirmed + rejected + unknown
    reconciliation = (
        calling > 0
        or unsettled > 0
        or unknown > 0
        or unknown_budget > 0
        or (open_budget > 0 and unsettled == 0)
    )
    prerequisites = entitlement_count == 1 and api_key_configured
    return {
        "voice_transcription_enabled": enabled,
        "voice_transcription_data_gate_approved": data_gate_approved,
        "voice_transcription_max_calls": max_calls,
        "voice_transcription_prerequisites_ready": prerequisites,
        "voice_transcription_gate_ready": (
            prerequisites
            and enabled
            and data_gate_approved
            and max_calls == 1
            and total == 0
        ),
        "voice_transcription_attempt_count": total,
        "voice_transcription_call_boundary_count": call_boundary,
        "voice_transcription_confirmed_count": confirmed,
        "voice_transcription_rejected_count": rejected,
        "voice_transcription_unknown_count": unknown,
        "voice_transcription_blocked_count": blocked,
        "voice_transcription_settlement_pending_count": unsettled,
        "voice_transcription_ready": confirmed == 1,
        "voice_transcription_reconciliation_required": reconciliation,
    }


async def _attempt(
    session: AsyncSession,
    attempt_id: str,
) -> BecomeVoiceTranscriptionAttemptModel:
    try:
        parsed = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise BecomeControlledIntakeError(
            "become_transcription_attempt_id_invalid"
        ) from exc
    attempt = await session.scalar(
        select(BecomeVoiceTranscriptionAttemptModel)
        .where(BecomeVoiceTranscriptionAttemptModel.id == parsed)
        .with_for_update()
    )
    if attempt is None:
        raise BecomeControlledIntakeError(
            "become_transcription_attempt_not_found"
        )
    return attempt


async def _workspace_key(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> str:
    value = await session.scalar(
        select(Workspace.key).where(Workspace.id == workspace_id)
    )
    if value is None:
        raise BecomeControlledIntakeError(
            "become_transcription_workspace_not_found"
        )
    return str(value)


async def _work(
    session: AsyncSession,
    attempt: BecomeVoiceTranscriptionAttemptModel,
    workspace_key: str,
) -> BecomeVoiceTranscriptionWork:
    correlation = await session.scalar(
        select(BecomeVoiceCorrelationModel).where(
            BecomeVoiceCorrelationModel.id == attempt.correlation_id
        )
    )
    intake = await session.scalar(
        select(BecomeMediaIntakeModel).where(
            BecomeMediaIntakeModel.id == attempt.media_intake_id
        )
    )
    if correlation is None or intake is None:
        raise BecomeControlledIntakeError(
            "become_transcription_binding_missing"
        )
    return _make_work(
        attempt,
        workspace_key=workspace_key,
        correlation_ref=correlation.correlation_ref,
        intake=intake,
    )


def _make_work(
    attempt: BecomeVoiceTranscriptionAttemptModel,
    *,
    workspace_key: str,
    correlation_ref: str,
    intake: BecomeMediaIntakeModel,
) -> BecomeVoiceTranscriptionWork:
    return BecomeVoiceTranscriptionWork(
        attempt_id=str(attempt.id),
        workspace_key=workspace_key,
        correlation_ref=correlation_ref,
        intake_key=intake.intake_key,
        state=BecomeVoiceTranscriptionState(attempt.state),
        reservation_key=attempt.reservation_key,
        period_key=attempt.period_key,
        requested_microusd=attempt.requested_microusd,
        occurred_at=attempt.created_at,
        model=attempt.model,
        language=attempt.language,
        raw_data=intake.raw_data or b"",
        raw_media_type=intake.raw_media_type or "audio/ogg",
        provider_call_started_at=attempt.provider_call_started_at,
        provider_operation_ref=attempt.provider_operation_ref,
        billed_quantity=attempt.billed_quantity,
        actual_microusd=attempt.actual_microusd,
    )
