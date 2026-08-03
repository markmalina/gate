"""PostgreSQL and Telegram adapters for one BECOME free result."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
)
from app.application.become_free_result import (
    FREE_RESULT_DELIVERY_SCHEMA,
    FREE_RESULT_SCHEMA,
    BecomeFreeResultCandidate,
    BecomeFreeResultProviderError,
    BecomeFreeResultReservation,
    BecomeFreeResultState,
    build_artifact_receipt,
    build_delivery_receipt,
    build_free_result_text,
    canonical_digest,
    result_digest,
    validate_receipt_replay,
)
from app.application.become_i4_chain import (
    BecomeI4Stage,
    validate_i4_receipt,
)
from app.infrastructure.become_i4_chain import TAKE_SOURCE_SHA
from app.infrastructure.become_i4_recovery_execution import (
    validate_stage_event_replay,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
    BecomeFreeResultArtifactModel,
    BecomeFreeResultDeliveryModel,
    BecomeMediaIntakeModel,
    BecomeVoiceCorrelationModel,
)
from app.models.become_recovery import (
    BecomeI4RecoveryAttemptModel,
    BecomeI4RecoveryStageEventModel,
)
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.services.telegram import TelegramApiError, TelegramClient


@dataclass(frozen=True, slots=True)
class BecomeFreeResultArtifactProjection:
    artifact_digest: str
    result_digest: str
    take_receipt_digest: str
    duplicate: bool


_EligibleRow = tuple[
    AgentInstallationModel,
    Dialog,
    BecomeControlledConsentReceiptModel,
    BecomeMediaIntakeModel,
    BecomeVoiceCorrelationModel,
    BecomeI4RecoveryAttemptModel,
    BecomeI4RecoveryStageEventModel,
    BecomeI4RecoveryStageEventModel,
]


class SessionFactoryBecomeFreeResultStore:
    """Project once, then reserve one destination-bound Telegram delivery."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_artifact(
        self,
        *,
        workspace_key: str,
        now: datetime,
    ) -> BecomeFreeResultArtifactProjection | None:
        async with self._sessions.begin() as session:
            rows = await _eligible_rows(
                session,
                workspace_key=workspace_key,
                lock=True,
            )
            if not rows:
                return None
            if len(rows) != 1:
                raise BecomeControlledIntakeError(
                    "become_free_result_source_cardinality_invalid"
                )
            row = rows[0]
            source = _validate_source(row)
            existing = await session.scalar(
                select(BecomeFreeResultArtifactModel)
                .where(
                    BecomeFreeResultArtifactModel.correlation_id
                    == source.correlation.id
                )
                .with_for_update()
            )
            if existing is not None:
                _validate_artifact(existing, source=source)
                return _projection(existing, duplicate=True)

            result_text = build_free_result_text(
                _required_text(source.media.confirmed_transcript)
            )
            exact_result_digest = result_digest(result_text)
            receipt = build_artifact_receipt(
                correlation_ref=source.correlation.correlation_ref,
                take_receipt_digest=source.take_terminal.receipt_digest
                or "",
                transcript_digest=source.media.transcript_digest or "",
                exact_result_digest=exact_result_digest,
            )
            artifact = BecomeFreeResultArtifactModel(
                workspace_id=source.attempt.workspace_id,
                installation_id=source.installation.id,
                dialog_id=source.dialog.id,
                correlation_id=source.correlation.id,
                recovery_attempt_id=source.attempt.id,
                take_event_id=source.take_terminal.id,
                schema_name=FREE_RESULT_SCHEMA,
                result_key=_result_key(
                    source.correlation.correlation_ref,
                    exact_result_digest,
                ),
                result_digest=exact_result_digest,
                result_text=result_text,
                transcript_digest=source.media.transcript_digest or "",
                take_receipt_digest=(
                    source.take_terminal.receipt_digest or ""
                ),
                destination_binding_digest=(
                    source.destination_binding_digest
                ),
                receipt_digest=canonical_digest(receipt),
                receipt_payload=receipt,
                created_at=now,
            )
            session.add(artifact)
            await session.flush()
            return _projection(artifact, duplicate=False)

    async def candidate(
        self,
        *,
        workspace_key: str,
    ) -> BecomeFreeResultCandidate | None:
        async with self._sessions() as session:
            rows = await _eligible_rows(
                session,
                workspace_key=workspace_key,
                lock=False,
            )
            if not rows:
                return None
            if len(rows) != 1:
                raise BecomeControlledIntakeError(
                    "become_free_result_source_cardinality_invalid"
                )
            source = _validate_source(rows[0])
            artifact = await session.scalar(
                select(BecomeFreeResultArtifactModel).where(
                    BecomeFreeResultArtifactModel.correlation_id
                    == source.correlation.id
                )
            )
            if artifact is None:
                return None
            _validate_artifact(artifact, source=source)
            return _candidate(artifact, source=source)

    async def reserve(
        self,
        *,
        workspace_key: str,
        candidate_key: str,
        now: datetime,
    ) -> BecomeFreeResultReservation:
        async with self._sessions.begin() as session:
            rows = await _eligible_rows(
                session,
                workspace_key=workspace_key,
                lock=True,
            )
            if len(rows) != 1:
                raise BecomeControlledIntakeError(
                    "become_free_result_delivery_gate_not_ready"
                )
            source = _validate_source(rows[0])
            artifact = await session.scalar(
                select(BecomeFreeResultArtifactModel)
                .where(
                    BecomeFreeResultArtifactModel.correlation_id
                    == source.correlation.id
                )
                .with_for_update()
            )
            if artifact is None:
                raise BecomeControlledIntakeError(
                    "become_free_result_artifact_missing"
                )
            _validate_artifact(artifact, source=source)
            expected_key = _candidate_key(artifact)
            if candidate_key != expected_key:
                raise BecomeControlledIntakeError(
                    "become_free_result_candidate_changed"
                )
            existing = await session.scalar(
                select(BecomeFreeResultDeliveryModel)
                .where(
                    BecomeFreeResultDeliveryModel.artifact_id
                    == artifact.id
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.delivery_key != _delivery_key(artifact):
                    raise BecomeControlledIntakeError(
                        "become_free_result_delivery_mutated_replay"
                    )
                if existing.state == BecomeFreeResultState.SENDING.value:
                    _set_terminal(
                        existing,
                        artifact=artifact,
                        state=BecomeFreeResultState.UNKNOWN,
                        finished_at=now,
                        provider_message_id=None,
                        error_code=(
                            "become_free_result_prior_outcome_unknown"
                        ),
                    )
                    await session.flush()
                else:
                    _validate_delivery(existing, artifact=artifact)
                return _reservation(existing, idempotent_replay=True)

            delivery = BecomeFreeResultDeliveryModel(
                workspace_id=artifact.workspace_id,
                artifact_id=artifact.id,
                installation_id=artifact.installation_id,
                dialog_id=artifact.dialog_id,
                schema_name=FREE_RESULT_DELIVERY_SCHEMA,
                delivery_key=_delivery_key(artifact),
                state=BecomeFreeResultState.SENDING.value,
                max_calls=1,
                destination_binding_digest=(
                    artifact.destination_binding_digest
                ),
                created_at=now,
                updated_at=now,
            )
            session.add(delivery)
            await session.flush()
            return BecomeFreeResultReservation(
                attempt_key=f"become-free-result-{delivery.id.hex}",
                state=BecomeFreeResultState.SENDING,
                target_chat_id=source.dialog.provider_dialog_id,
                result_text=artifact.result_text,
            )

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: BecomeFreeResultState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> BecomeFreeResultReservation:
        if state is BecomeFreeResultState.SENDING:
            raise BecomeControlledIntakeError(
                "become_free_result_terminal_state_required"
            )
        _validate_outcome(
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )
        delivery_id = _attempt_id(attempt_key)
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        BecomeFreeResultDeliveryModel,
                        BecomeFreeResultArtifactModel,
                    )
                    .join(
                        BecomeFreeResultArtifactModel,
                        BecomeFreeResultArtifactModel.id
                        == BecomeFreeResultDeliveryModel.artifact_id,
                    )
                    .join(
                        Workspace,
                        Workspace.id
                        == BecomeFreeResultDeliveryModel.workspace_id,
                    )
                    .where(
                        BecomeFreeResultDeliveryModel.id == delivery_id,
                        Workspace.key == workspace_key,
                        Workspace.is_active.is_(True),
                    )
                    .with_for_update(
                        of=BecomeFreeResultDeliveryModel
                    )
                )
            ).one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_free_result_delivery_not_found"
                )
            delivery, artifact = row
            if delivery.state != BecomeFreeResultState.SENDING.value:
                _validate_delivery(delivery, artifact=artifact)
                if (
                    delivery.state,
                    delivery.provider_message_id,
                    delivery.error_code,
                ) != (state.value, provider_message_id, error_code):
                    raise BecomeControlledIntakeError(
                        "become_free_result_delivery_outcome_conflict"
                    )
                return _reservation(
                    delivery,
                    idempotent_replay=True,
                )
            _set_terminal(
                delivery,
                artifact=artifact,
                state=state,
                finished_at=finished_at,
                provider_message_id=provider_message_id,
                error_code=error_code,
            )
            await session.flush()
            return _reservation(delivery, idempotent_replay=False)


class TelegramClientBecomeFreeResultAdapter:
    """Expose one plain-text send without logging private result content."""

    def __init__(self, telegram: TelegramClient) -> None:
        self._telegram = telegram

    async def send_text(self, *, chat_id: str, text: str) -> str:
        if result_digest(text) == "":
            raise BecomeControlledIntakeError(
                "become_free_result_text_invalid"
            )
        try:
            response = await self._telegram.send_plain_message(
                chat_id=chat_id,
                text=text,
            )
        except TelegramApiError as exc:
            code = exc.error_code
            state = BecomeFreeResultState.FAILED
            if code in {400, 403}:
                state = BecomeFreeResultState.BLOCKED
            elif code is None or code == 429 or code >= 500:
                state = BecomeFreeResultState.UNKNOWN
            raise BecomeFreeResultProviderError(
                state=state,
                error_code=(
                    f"telegram_api_{code}"
                    if code is not None
                    else "telegram_api_unknown"
                ),
            ) from None
        except (httpx.TransportError, RuntimeError):
            raise BecomeFreeResultProviderError(
                state=BecomeFreeResultState.UNKNOWN,
                error_code="telegram_outcome_unknown",
            ) from None
        result = response.get("result")
        message_id = (
            result.get("message_id")
            if isinstance(result, dict)
            else None
        )
        if (
            not isinstance(message_id, (int, str))
            or isinstance(message_id, bool)
            or not str(message_id).strip()
        ):
            raise BecomeFreeResultProviderError(
                state=BecomeFreeResultState.UNKNOWN,
                error_code="telegram_message_id_invalid",
            )
        return str(message_id)


async def become_free_result_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    delivery_enabled: bool,
    max_calls: int,
) -> dict[str, object]:
    artifacts = list(
        (
            await session.scalars(
                select(BecomeFreeResultArtifactModel)
                .join(
                    Workspace,
                    Workspace.id
                    == BecomeFreeResultArtifactModel.workspace_id,
                )
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
            )
        ).all()
    )
    if len(artifacts) > 1:
        raise ValueError("become_free_result_artifact_cardinality_invalid")
    artifact = artifacts[0] if artifacts else None
    if artifact is not None:
        validate_receipt_replay(
            artifact.receipt_payload,
            expected_digest=artifact.receipt_digest,
        )
        if result_digest(artifact.result_text) != artifact.result_digest:
            raise ValueError("become_free_result_private_artifact_mutated")
    deliveries = list(
        (
            await session.scalars(
                select(BecomeFreeResultDeliveryModel)
                .join(
                    Workspace,
                    Workspace.id
                    == BecomeFreeResultDeliveryModel.workspace_id,
                )
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
            )
        ).all()
    )
    if len(deliveries) > 1:
        raise ValueError("become_free_result_delivery_cardinality_invalid")
    delivery = deliveries[0] if deliveries else None
    if delivery is not None:
        if artifact is None or delivery.artifact_id != artifact.id:
            raise ValueError("become_free_result_delivery_orphaned")
        if delivery.state != BecomeFreeResultState.SENDING.value:
            _validate_delivery(delivery, artifact=artifact)
    state = delivery.state if delivery is not None else "not_started"
    sent_count = sum(
        item.state == BecomeFreeResultState.SENT.value
        for item in deliveries
    )
    unknown_count = sum(
        item.state == BecomeFreeResultState.UNKNOWN.value
        for item in deliveries
    )
    return {
        "free_result_artifact_count": len(artifacts),
        "free_result_artifact_ready": len(artifacts) == 1,
        "free_result_schema": (
            artifact.schema_name if artifact is not None else None
        ),
        "free_result_digest": (
            artifact.result_digest if artifact is not None else None
        ),
        "free_result_receipt_digest": (
            artifact.receipt_digest if artifact is not None else None
        ),
        "free_result_take_receipt_digest": (
            artifact.take_receipt_digest if artifact is not None else None
        ),
        "free_result_delivery_enabled": delivery_enabled,
        "free_result_activation_max_calls": max_calls,
        "free_result_delivery_count": len(deliveries),
        "free_result_delivery_state": state,
        "free_result_delivery_sent_count": sent_count,
        "free_result_delivery_unknown_count": unknown_count,
        "free_result_delivery_receipt_digest": (
            delivery.receipt_digest if delivery is not None else None
        ),
        "free_result_reconciliation_required": unknown_count > 0,
    }


@dataclass(frozen=True, slots=True)
class _ValidatedSource:
    installation: AgentInstallationModel
    dialog: Dialog
    consent: BecomeControlledConsentReceiptModel
    media: BecomeMediaIntakeModel
    correlation: BecomeVoiceCorrelationModel
    attempt: BecomeI4RecoveryAttemptModel
    take_reservation: BecomeI4RecoveryStageEventModel
    take_terminal: BecomeI4RecoveryStageEventModel
    destination_binding_digest: str


def _validate_source(row: _EligibleRow) -> _ValidatedSource:
    (
        installation,
        dialog,
        consent,
        media,
        correlation,
        attempt,
        take_reservation,
        take_terminal,
    ) = row
    validate_stage_event_replay(
        take_reservation.event_payload,
        expected_digest=take_reservation.event_digest,
    )
    validate_stage_event_replay(
        take_terminal.event_payload,
        expected_digest=take_terminal.event_digest,
    )
    if (
        take_terminal.receipt_payload is None
        or take_terminal.receipt_digest is None
        or take_reservation.request_payload
        != take_terminal.request_payload
        or take_reservation.request_digest
        != take_terminal.request_digest
        or canonical_digest(take_terminal.receipt_payload)
        != take_terminal.receipt_digest
    ):
        raise BecomeControlledIntakeError(
            "become_free_result_take_event_invalid"
        )
    validate_i4_receipt(
        stage=BecomeI4Stage.TAKE,
        receipt=take_terminal.receipt_payload,
        request=take_reservation.request_payload,
        expected_source_sha=TAKE_SOURCE_SHA,
    )
    if (
        attempt.attempt_version != 2
        or take_reservation.attempt_id != attempt.id
        or take_terminal.attempt_id != attempt.id
        or media.transcript_state != "confirmed"
        or media.confirmed_transcript is None
        or media.transcript_digest != attempt.transcript_digest
    ):
        raise BecomeControlledIntakeError(
            "become_free_result_source_binding_invalid"
        )
    destination = canonical_digest(
        {
            "schema": "gate.become-free-result-destination@1",
            "workspace_id": str(attempt.workspace_id),
            "installation_id": str(installation.id),
            "dialog_id": str(dialog.id),
            "connector_id": str(dialog.connector_id),
            "provider_dialog_id": dialog.provider_dialog_id,
            "consent_receipt_digest": consent.receipt_digest,
            "group_state": consent.group_state,
            "member_count": consent.member_count,
        }
    )
    return _ValidatedSource(
        installation=installation,
        dialog=dialog,
        consent=consent,
        media=media,
        correlation=correlation,
        attempt=attempt,
        take_reservation=take_reservation,
        take_terminal=take_terminal,
        destination_binding_digest=destination,
    )


async def _eligible_rows(
    session: AsyncSession,
    *,
    workspace_key: str,
    lock: bool,
) -> list[_EligibleRow]:
    reservation = aliased(BecomeI4RecoveryStageEventModel)
    terminal = aliased(BecomeI4RecoveryStageEventModel)
    statement = (
        select(
            AgentInstallationModel,
            Dialog,
            BecomeControlledConsentReceiptModel,
            BecomeMediaIntakeModel,
            BecomeVoiceCorrelationModel,
            BecomeI4RecoveryAttemptModel,
            reservation,
            terminal,
        )
        .select_from(Workspace)
        .join(
            TransportConnector,
            TransportConnector.workspace_id == Workspace.id,
        )
        .join(
            Dialog,
            (Dialog.workspace_id == Workspace.id)
            & (Dialog.connector_id == TransportConnector.id),
        )
        .join(
            AgentInstallationModel,
            (AgentInstallationModel.workspace_id == Workspace.id)
            & (AgentInstallationModel.connector_id == TransportConnector.id)
            & (AgentInstallationModel.dialog_id == Dialog.id),
        )
        .join(
            BecomeControlledIntakeReceiptModel,
            BecomeControlledIntakeReceiptModel.installation_id
            == AgentInstallationModel.id,
        )
        .join(
            BecomeControlledConsentReceiptModel,
            (BecomeControlledConsentReceiptModel.installation_id
             == AgentInstallationModel.id)
            & (BecomeControlledConsentReceiptModel.subject_ref
               == BecomeControlledIntakeReceiptModel.subject_ref),
        )
        .join(
            BecomeMediaIntakeModel,
            BecomeMediaIntakeModel.installation_id
            == AgentInstallationModel.id,
        )
        .join(
            BecomeVoiceCorrelationModel,
            BecomeVoiceCorrelationModel.media_intake_id
            == BecomeMediaIntakeModel.id,
        )
        .join(
            BecomeI4RecoveryAttemptModel,
            BecomeI4RecoveryAttemptModel.correlation_id
            == BecomeVoiceCorrelationModel.id,
        )
        .join(
            reservation,
            (reservation.attempt_id == BecomeI4RecoveryAttemptModel.id)
            & (reservation.stage == "take")
            & (reservation.event_sequence == 1)
            & (reservation.event_type == "reserved"),
        )
        .join(
            terminal,
            (terminal.attempt_id == BecomeI4RecoveryAttemptModel.id)
            & (terminal.stage == "take")
            & (terminal.event_sequence == 2)
            & (terminal.event_type == "completed"),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key == BECOME_CONNECTOR_KEY,
            TransportConnector.connector_kind == "telegram_bot",
            TransportConnector.is_enabled.is_(True),
            Dialog.dialog_kind.in_(("group", "supergroup")),
            AgentInstallationModel.state == "active",
            BecomeControlledIntakeReceiptModel.intent
            == "registration_confirmed",
            BecomeControlledIntakeReceiptModel.group_state == "owner_only",
            BecomeControlledIntakeReceiptModel.member_count == 2,
            BecomeControlledConsentReceiptModel.intent
            == "consent_accepted",
            BecomeControlledConsentReceiptModel.group_state == "owner_only",
            BecomeControlledConsentReceiptModel.member_count == 2,
            BecomeMediaIntakeModel.transcript_state == "confirmed",
            BecomeI4RecoveryAttemptModel.attempt_version == 2,
        )
        .limit(2)
    )
    if lock:
        statement = statement.with_for_update(
            of=BecomeI4RecoveryAttemptModel
        )
    return list((await session.execute(statement)).tuples().all())


def _validate_artifact(
    artifact: BecomeFreeResultArtifactModel,
    *,
    source: _ValidatedSource,
) -> None:
    receipt = validate_receipt_replay(
        artifact.receipt_payload,
        expected_digest=artifact.receipt_digest,
    )
    expected_text = build_free_result_text(
        _required_text(source.media.confirmed_transcript)
    )
    if (
        artifact.schema_name != FREE_RESULT_SCHEMA
        or artifact.workspace_id != source.attempt.workspace_id
        or artifact.installation_id != source.installation.id
        or artifact.dialog_id != source.dialog.id
        or artifact.correlation_id != source.correlation.id
        or artifact.recovery_attempt_id != source.attempt.id
        or artifact.take_event_id != source.take_terminal.id
        or artifact.result_text != expected_text
        or artifact.result_digest != result_digest(expected_text)
        or artifact.transcript_digest != source.media.transcript_digest
        or artifact.take_receipt_digest
        != source.take_terminal.receipt_digest
        or artifact.destination_binding_digest
        != source.destination_binding_digest
        or receipt["result_digest"] != artifact.result_digest
        or receipt["take_receipt_digest"]
        != artifact.take_receipt_digest
    ):
        raise BecomeControlledIntakeError(
            "become_free_result_artifact_mutated_replay"
        )


def _candidate(
    artifact: BecomeFreeResultArtifactModel,
    *,
    source: _ValidatedSource,
) -> BecomeFreeResultCandidate:
    return BecomeFreeResultCandidate(
        candidate_key=_candidate_key(artifact),
        correlation_ref=source.correlation.correlation_ref,
        result_digest=artifact.result_digest,
        take_receipt_digest=artifact.take_receipt_digest,
        destination_binding_digest=artifact.destination_binding_digest,
        installation_ref=source.installation.installation_key,
        target_chat_id=source.dialog.provider_dialog_id,
    )


def _candidate_key(artifact: BecomeFreeResultArtifactModel) -> str:
    return "become_free_result_" + canonical_digest(
        {
            "artifact_receipt_digest": artifact.receipt_digest,
            "destination_binding_digest": (
                artifact.destination_binding_digest
            ),
        }
    )[:24]


def _result_key(correlation_ref: str, exact_result_digest: str) -> str:
    return "become_result_" + canonical_digest(
        {
            "correlation_ref": correlation_ref,
            "result_digest": exact_result_digest,
        }
    )[:24]


def _delivery_key(artifact: BecomeFreeResultArtifactModel) -> str:
    return "become_delivery_" + canonical_digest(
        {
            "result_digest": artifact.result_digest,
            "take_receipt_digest": artifact.take_receipt_digest,
            "destination_binding_digest": (
                artifact.destination_binding_digest
            ),
        }
    )[:24]


def _set_terminal(
    delivery: BecomeFreeResultDeliveryModel,
    *,
    artifact: BecomeFreeResultArtifactModel,
    state: BecomeFreeResultState,
    finished_at: datetime,
    provider_message_id: str | None,
    error_code: str | None,
) -> None:
    _validate_outcome(
        state=state,
        provider_message_id=provider_message_id,
        error_code=error_code,
    )
    receipt = build_delivery_receipt(
        correlation_ref=str(
            artifact.receipt_payload["correlation_ref"]
        ),
        exact_result_digest=artifact.result_digest,
        take_receipt_digest=artifact.take_receipt_digest,
        destination_binding_digest=artifact.destination_binding_digest,
        state=state,
    )
    delivery.state = state.value
    delivery.provider_message_id = provider_message_id
    delivery.error_code = error_code
    delivery.receipt_digest = canonical_digest(receipt)
    delivery.receipt_payload = receipt
    delivery.sent_at = (
        finished_at if state is BecomeFreeResultState.SENT else None
    )
    delivery.finished_at = finished_at
    delivery.updated_at = finished_at


def _validate_delivery(
    delivery: BecomeFreeResultDeliveryModel,
    *,
    artifact: BecomeFreeResultArtifactModel,
) -> None:
    if delivery.receipt_payload is None or delivery.receipt_digest is None:
        raise BecomeControlledIntakeError(
            "become_free_result_delivery_receipt_missing"
        )
    receipt = validate_receipt_replay(
        delivery.receipt_payload,
        expected_digest=delivery.receipt_digest,
        delivery=True,
    )
    if (
        delivery.schema_name != FREE_RESULT_DELIVERY_SCHEMA
        or delivery.artifact_id != artifact.id
        or delivery.installation_id != artifact.installation_id
        or delivery.dialog_id != artifact.dialog_id
        or delivery.max_calls != 1
        or delivery.destination_binding_digest
        != artifact.destination_binding_digest
        or delivery.delivery_key != _delivery_key(artifact)
        or receipt["result_digest"] != artifact.result_digest
        or receipt["take_receipt_digest"]
        != artifact.take_receipt_digest
        or receipt["classification"] != delivery.state
    ):
        raise BecomeControlledIntakeError(
            "become_free_result_delivery_mutated_replay"
        )


def _validate_outcome(
    *,
    state: BecomeFreeResultState,
    provider_message_id: str | None,
    error_code: str | None,
) -> None:
    if state is BecomeFreeResultState.SENT:
        if (
            provider_message_id is None
            or not provider_message_id.strip()
            or len(provider_message_id) > 255
            or error_code is not None
        ):
            raise BecomeControlledIntakeError(
                "become_free_result_success_invalid"
            )
        return
    if state is BecomeFreeResultState.SENDING:
        raise BecomeControlledIntakeError(
            "become_free_result_terminal_state_required"
        )
    if provider_message_id is not None or not error_code:
        raise BecomeControlledIntakeError(
            "become_free_result_failure_invalid"
        )


def _reservation(
    delivery: BecomeFreeResultDeliveryModel,
    *,
    idempotent_replay: bool,
) -> BecomeFreeResultReservation:
    return BecomeFreeResultReservation(
        attempt_key=f"become-free-result-{delivery.id.hex}",
        state=BecomeFreeResultState(delivery.state),
        provider_message_id=delivery.provider_message_id,
        error_code=delivery.error_code,
        idempotent_replay=idempotent_replay,
    )


def _attempt_id(attempt_key: str) -> uuid.UUID:
    if not attempt_key.startswith("become-free-result-"):
        raise BecomeControlledIntakeError(
            "become_free_result_attempt_key_invalid"
        )
    try:
        return uuid.UUID(
            attempt_key.removeprefix("become-free-result-")
        )
    except ValueError as exc:
        raise BecomeControlledIntakeError(
            "become_free_result_attempt_key_invalid"
        ) from exc


def _projection(
    artifact: BecomeFreeResultArtifactModel,
    *,
    duplicate: bool,
) -> BecomeFreeResultArtifactProjection:
    return BecomeFreeResultArtifactProjection(
        artifact_digest=artifact.receipt_digest,
        result_digest=artifact.result_digest,
        take_receipt_digest=artifact.take_receipt_digest,
        duplicate=duplicate,
    )


def _required_text(value: str | None) -> str:
    if value is None:
        raise BecomeControlledIntakeError(
            "become_free_result_transcript_missing"
        )
    return value
