"""Durable SQL store for the owner-only Universal Mayak private runtime.

The adapter reuses Gate's canonical message, Agent Kernel, installation,
delivery-grant and agent-send-attempt tables.  It never chooses a provider
target and performs no external call.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.universal_mayak_demo import (
    MAX_CONTEXT_MESSAGES,
    DemoContextMessage,
    DemoDeliveryReceipt,
    DemoDeliveryState,
    DemoDisposition,
    DemoInstallationState,
    DemoReservationState,
    DemoScope,
    DemoTerminalReceipt,
    DemoTurnReservation,
    ProcessUniversalMayakCommand,
    UniversalMayakDemoError,
    UniversalMayakInstallation,
)
from app.models.agent import (
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
    InstructionRevisionModel,
)
from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)
from app.models.canonical import CanonicalMessage, Dialog, Workspace

_PROMPT_REVISION = "universal-mayak-oko-v2"
_DISCLOSURE = (
    "Продолжая диалог, вы соглашаетесь, что в нём работает автоматизация "
    "с использованием ИИ."
)
_INPUT_DIGEST_PREFIX = "mayak-input-sha256:"


@dataclass(frozen=True, slots=True)
class _InstallationBinding:
    deployment_id: uuid.UUID
    instruction_revision_id: uuid.UUID
    state: str
    dialog_label: str


class SessionFactoryUniversalMayakDemoStore:
    """Implement UniversalMayakDemoStore with commit-before-send boundaries."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def resolve_installation(
        self,
        scope: DemoScope,
    ) -> UniversalMayakInstallation:
        workspace_id, installation_id, dialog_id = _scope_ids(scope)
        async with self._sessions() as session:
            row = await _binding(
                session,
                workspace_id=workspace_id,
                installation_id=installation_id,
                dialog_id=dialog_id,
            )
            prior_sent_attempt = await session.scalar(
                select(AgentSendAttemptModel.id)
                .join(
                    DeliveryGrantModel,
                    DeliveryGrantModel.id == AgentSendAttemptModel.grant_id,
                )
                .where(
                    DeliveryGrantModel.workspace_id == workspace_id,
                    DeliveryGrantModel.installation_id == installation_id,
                    AgentSendAttemptModel.state == "sent",
                )
                .limit(1)
            )
            session_state = await session.scalar(
                select(AgentSessionModel.state).where(
                    AgentSessionModel.workspace_id == workspace_id,
                    AgentSessionModel.dialog_id == dialog_id,
                    AgentSessionModel.deployment_id == row.deployment_id,
                    AgentSessionModel.instruction_revision_id
                    == row.instruction_revision_id,
                    AgentSessionModel.state == "handoff_required",
                )
            )
        state = _installation_state(row.state, session_state)
        return UniversalMayakInstallation(
            scope=scope,
            public_label=row.dialog_label,
            disclosure_text=_DISCLOSURE,
            state=state,
            disclosure_required=prior_sent_attempt is None,
            owner_only=True,
            dialog_kind="private",
        )

    async def reserve_turn(
        self,
        command: ProcessUniversalMayakCommand,
        *,
        input_sha256: str,
    ) -> DemoTurnReservation:
        _require_digest(input_sha256, "input_sha256")
        workspace_id, installation_id, dialog_id = _scope_ids(command.scope)
        trigger_id = _event_uuid(command.event_ref)
        if _idempotency_uuid(command.idempotency_key) != trigger_id:
            raise UniversalMayakDemoError(
                "mayak_demo_idempotency_binding_invalid"
            )
        marker = _INPUT_DIGEST_PREFIX + input_sha256
        async with self._sessions.begin() as session:
            binding = await _binding(
                session,
                workspace_id=workspace_id,
                installation_id=installation_id,
                dialog_id=dialog_id,
                for_update=True,
            )
            canonical = await session.scalar(
                select(CanonicalMessage).where(
                    CanonicalMessage.id == trigger_id,
                    CanonicalMessage.workspace_id == workspace_id,
                    CanonicalMessage.dialog_id == dialog_id,
                    CanonicalMessage.direction == "inbound",
                    CanonicalMessage.text_content == command.text,
                )
            )
            if canonical is None:
                raise UniversalMayakDemoError("mayak_demo_trigger_binding_invalid")

            session_key = _stable_key("mayak-session", installation_id)
            session_id = await session.scalar(
                insert(AgentSessionModel)
                .values(
                    workspace_id=workspace_id,
                    dialog_id=dialog_id,
                    deployment_id=binding.deployment_id,
                    instruction_revision_id=binding.instruction_revision_id,
                    session_key=session_key,
                    state="active",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AgentSessionModel.workspace_id,
                        AgentSessionModel.session_key,
                    ]
                )
                .returning(AgentSessionModel.id)
            )
            if session_id is None:
                existing_session = await session.scalar(
                    select(AgentSessionModel).where(
                        AgentSessionModel.workspace_id == workspace_id,
                        AgentSessionModel.session_key == session_key,
                    )
                )
                if (
                    existing_session is None
                    or existing_session.dialog_id != dialog_id
                    or existing_session.deployment_id != binding.deployment_id
                    or existing_session.instruction_revision_id
                    != binding.instruction_revision_id
                ):
                    raise UniversalMayakDemoError(
                        "mayak_demo_session_binding_conflict"
                    )
                session_id = existing_session.id

            turn_id = await session.scalar(
                insert(AgentTurnModel)
                .values(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    trigger_message_id=trigger_id,
                    instruction_revision_id=binding.instruction_revision_id,
                    context_message_ids=[marker],
                    status="running",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AgentTurnModel.session_id,
                        AgentTurnModel.trigger_message_id,
                    ]
                )
                .returning(AgentTurnModel.id)
            )
            if turn_id is not None:
                return DemoTurnReservation(
                    DemoReservationState.NEW,
                    str(turn_id),
                    input_sha256,
                )

            turn = await session.scalar(
                select(AgentTurnModel).where(
                    AgentTurnModel.session_id == session_id,
                    AgentTurnModel.trigger_message_id == trigger_id,
                )
            )
            if turn is None:
                raise UniversalMayakDemoError("mayak_demo_turn_reservation_failed")
            stored_digest = _stored_input_digest(turn.context_message_ids)
            if stored_digest != input_sha256:
                raise UniversalMayakDemoError("mayak_demo_replay_conflict")
            grant, attempt = await _delivery_state(
                session,
                workspace_id=workspace_id,
                installation_id=installation_id,
                trigger_id=trigger_id,
            )
            return _existing_reservation(turn, input_sha256, grant, attempt)

    async def load_context(
        self,
        scope: DemoScope,
        *,
        observed_at: datetime,
    ) -> tuple[DemoContextMessage, ...]:
        workspace_id, installation_id, dialog_id = _scope_ids(scope)
        async with self._sessions() as session:
            await _binding(
                session,
                workspace_id=workspace_id,
                installation_id=installation_id,
                dialog_id=dialog_id,
            )
            rows = list(
                await session.scalars(
                    select(CanonicalMessage)
                    .where(
                        CanonicalMessage.workspace_id == workspace_id,
                        CanonicalMessage.dialog_id == dialog_id,
                        CanonicalMessage.text_content.is_not(None),
                        CanonicalMessage.direction.in_(("inbound", "outbound")),
                        CanonicalMessage.occurred_at <= observed_at,
                        CanonicalMessage.occurred_at
                        >= observed_at - timedelta(hours=24),
                        CanonicalMessage.id.not_in(
                            select(AgentTurnModel.trigger_message_id)
                            .join(
                                AgentSessionModel,
                                AgentSessionModel.id
                                == AgentTurnModel.session_id,
                            )
                            .where(
                                AgentTurnModel.workspace_id == workspace_id,
                                AgentTurnModel.status == "running",
                                AgentSessionModel.dialog_id == dialog_id,
                            )
                        ),
                    )
                    .order_by(CanonicalMessage.occurred_at.desc())
                    .limit(MAX_CONTEXT_MESSAGES)
                )
            )
        rows.reverse()
        context: list[DemoContextMessage] = []
        for message in rows:
            text = message.text_content
            if isinstance(text, str):
                context.append(
                    DemoContextMessage(
                        scope=scope,
                        message_ref=str(message.id),
                        role=(
                            "user"
                            if message.direction == "inbound"
                            else "assistant"
                        ),
                        text=text,
                        occurred_at=message.occurred_at,
                    )
                )
        return tuple(context)

    async def stage_reply(
        self,
        *,
        turn_ref: str,
        scope: DemoScope,
        candidate_text: str,
        candidate_sha256: str,
        now: datetime,
    ) -> str:
        turn_id = _uuid(turn_ref, "turn_ref")
        _require_digest(candidate_sha256, "candidate_sha256")
        if hashlib.sha256(candidate_text.encode("utf-8")).hexdigest() != candidate_sha256:
            raise UniversalMayakDemoError("mayak_demo_candidate_digest_mismatch")
        workspace_id, installation_id, dialog_id = _scope_ids(scope)
        async with self._sessions.begin() as session:
            binding = await _binding(
                session,
                workspace_id=workspace_id,
                installation_id=installation_id,
                dialog_id=dialog_id,
                for_update=True,
            )
            turn = await session.scalar(
                select(AgentTurnModel)
                .join(
                    AgentSessionModel,
                    AgentSessionModel.id == AgentTurnModel.session_id,
                )
                .where(
                    AgentTurnModel.id == turn_id,
                    AgentTurnModel.workspace_id == workspace_id,
                    AgentSessionModel.dialog_id == dialog_id,
                    AgentSessionModel.deployment_id == binding.deployment_id,
                    AgentTurnModel.status.in_(("running", "proposed")),
                )
                .with_for_update(of=AgentTurnModel)
            )
            if turn is None:
                raise UniversalMayakDemoError("mayak_demo_turn_binding_invalid")
            if turn.status == "proposed":
                if (
                    turn.candidate_text != candidate_text
                    or turn.candidate_sha256 != candidate_sha256
                ):
                    raise UniversalMayakDemoError(
                        "mayak_demo_candidate_replay_conflict"
                    )
            else:
                turn.status = "proposed"
                turn.result_kind = "reply"
                turn.result_code = "mayak_demo_reply_staged"
                turn.candidate_text = candidate_text
                turn.candidate_sha256 = candidate_sha256

            grant_key = _stable_key(
                "mayak-grant",
                installation_id,
                turn.trigger_message_id,
                candidate_sha256,
            )
            grant_id = await session.scalar(
                insert(DeliveryGrantModel)
                .values(
                    workspace_id=workspace_id,
                    installation_id=installation_id,
                    trigger_message_id=turn.trigger_message_id,
                    grant_key=grant_key,
                    candidate_sha256=candidate_sha256,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        DeliveryGrantModel.installation_id,
                        DeliveryGrantModel.trigger_message_id,
                        DeliveryGrantModel.candidate_sha256,
                    ]
                )
                .returning(DeliveryGrantModel.id)
            )
            if grant_id is None:
                grant = await session.scalar(
                    select(DeliveryGrantModel).where(
                        DeliveryGrantModel.workspace_id == workspace_id,
                        DeliveryGrantModel.installation_id == installation_id,
                        DeliveryGrantModel.trigger_message_id
                        == turn.trigger_message_id,
                        DeliveryGrantModel.candidate_sha256 == candidate_sha256,
                    )
                )
                if grant is None or grant.grant_key != grant_key:
                    raise UniversalMayakDemoError(
                        "mayak_demo_delivery_grant_conflict"
                    )
                grant_id = grant.id
            await session.scalar(
                insert(AgentSendAttemptModel)
                .values(
                    workspace_id=workspace_id,
                    grant_id=grant_id,
                    state="reserved",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[AgentSendAttemptModel.grant_id]
                )
                .returning(AgentSendAttemptModel.id)
            )
            return str(grant_id)

    async def finish_turn(
        self,
        *,
        turn_ref: str,
        terminal: DemoTerminalReceipt,
        now: datetime,
    ) -> None:
        turn_id = _uuid(turn_ref, "turn_ref")
        async with self._sessions.begin() as session:
            turn = await session.scalar(
                select(AgentTurnModel)
                .where(AgentTurnModel.id == turn_id)
                .with_for_update()
            )
            if turn is None:
                raise UniversalMayakDemoError("mayak_demo_turn_not_found")
            grant = await session.scalar(
                select(DeliveryGrantModel).where(
                    DeliveryGrantModel.workspace_id == turn.workspace_id,
                    DeliveryGrantModel.trigger_message_id
                    == turn.trigger_message_id,
                    DeliveryGrantModel.candidate_sha256
                    == turn.candidate_sha256,
                )
            )
            attempt = (
                await session.scalar(
                    select(AgentSendAttemptModel)
                    .where(AgentSendAttemptModel.grant_id == grant.id)
                    .with_for_update()
                )
                if grant is not None
                else None
            )
            existing = _terminal_from_models(turn, grant, attempt)
            if existing is not None:
                if existing != terminal:
                    raise UniversalMayakDemoError(
                        "mayak_demo_terminal_replay_conflict"
                    )
                return

            turn.result_code = terminal.code
            if terminal.disposition in {
                DemoDisposition.PAUSED,
                DemoDisposition.HUMAN_TAKEOVER,
            }:
                turn.status = "proposed"
                turn.result_kind = "handoff"
                turn.candidate_text = None
                turn.candidate_sha256 = None
                return
            if terminal.delivery is None:
                turn.status = "failed"
                turn.result_kind = "no_reply"
                turn.candidate_text = None
                turn.candidate_sha256 = None
                return
            if grant is None or attempt is None:
                raise UniversalMayakDemoError(
                    "mayak_demo_delivery_terminal_without_stage"
                )

            delivery = terminal.delivery
            attempt.state = delivery.state.value
            attempt.provider_message_id = delivery.provider_message_ref
            attempt.error_code = delivery.error_code
            attempt.updated_at = now
            if delivery.state is DemoDeliveryState.BLOCKED:
                grant.revoked_at = now
            else:
                grant.used_at = now
            turn.status = (
                "proposed"
                if delivery.state is DemoDeliveryState.SENT
                else "failed"
            )

    async def transition_installation(
        self,
        *,
        scope: DemoScope,
        target: DemoInstallationState,
        now: datetime,
    ) -> None:
        workspace_id, installation_id, dialog_id = _scope_ids(scope)
        async with self._sessions.begin() as session:
            binding = await _binding(
                session,
                workspace_id=workspace_id,
                installation_id=installation_id,
                dialog_id=dialog_id,
                for_update=True,
            )
            if target is DemoInstallationState.PAUSED:
                changed = await session.scalar(
                    update(AgentInstallationModel)
                    .where(
                        AgentInstallationModel.id == installation_id,
                        AgentInstallationModel.workspace_id == workspace_id,
                        AgentInstallationModel.state == "active",
                    )
                    .values(state="paused", updated_at=now)
                    .returning(AgentInstallationModel.id)
                )
                if changed is None and binding.state != "paused":
                    raise UniversalMayakDemoError(
                        "mayak_demo_pause_transition_conflict"
                    )
                return
            if target is not DemoInstallationState.HUMAN_TAKEOVER:
                raise UniversalMayakDemoError(
                    "mayak_demo_transition_not_supported"
                )
            changed = await session.scalar(
                update(AgentSessionModel)
                .where(
                    AgentSessionModel.workspace_id == workspace_id,
                    AgentSessionModel.dialog_id == dialog_id,
                    AgentSessionModel.deployment_id == binding.deployment_id,
                )
                .values(state="handoff_required")
                .returning(AgentSessionModel.id)
            )
            if changed is None:
                raise UniversalMayakDemoError(
                    "mayak_demo_handoff_session_missing"
                )


async def _binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    dialog_id: uuid.UUID,
    for_update: bool = False,
) -> _InstallationBinding:
    statement = (
        select(AgentInstallationModel)
        .join(Workspace, Workspace.id == AgentInstallationModel.workspace_id)
        .join(
            Dialog,
            (Dialog.id == AgentInstallationModel.dialog_id)
            & (Dialog.workspace_id == AgentInstallationModel.workspace_id),
        )
        .join(
            AgentDeploymentModel,
            (AgentDeploymentModel.id == AgentInstallationModel.deployment_id)
            & (
                AgentDeploymentModel.workspace_id
                == AgentInstallationModel.workspace_id
            ),
        )
        .join(
            InstructionRevisionModel,
            (
                InstructionRevisionModel.id
                == AgentInstallationModel.instruction_revision_id
            )
            & (
                InstructionRevisionModel.workspace_id
                == AgentInstallationModel.workspace_id
            )
            & (
                InstructionRevisionModel.id
                == AgentDeploymentModel.instruction_revision_id
            ),
        )
        .where(
            Workspace.id == workspace_id,
            Workspace.is_active.is_(True),
            AgentInstallationModel.id == installation_id,
            AgentInstallationModel.dialog_id == dialog_id,
            AgentInstallationModel.state.in_(("active", "paused")),
            Dialog.dialog_kind == "private",
            AgentDeploymentModel.state == "active",
            InstructionRevisionModel.revision_key == _PROMPT_REVISION,
            InstructionRevisionModel.validation_state == "validated",
        )
    )
    if for_update:
        statement = statement.with_for_update(of=AgentInstallationModel)
    rows = (await session.scalars(statement)).all()
    if len(rows) != 1:
        raise UniversalMayakDemoError("mayak_demo_installation_binding_invalid")
    row = rows[0]
    if row.deployment_id is None or row.instruction_revision_id is None:
        raise UniversalMayakDemoError("mayak_demo_installation_binding_invalid")
    return _InstallationBinding(
        deployment_id=row.deployment_id,
        instruction_revision_id=row.instruction_revision_id,
        state=row.state,
        dialog_label=row.dialog_label,
    )


async def _delivery_state(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    trigger_id: uuid.UUID,
) -> tuple[DeliveryGrantModel | None, AgentSendAttemptModel | None]:
    grant = await session.scalar(
        select(DeliveryGrantModel).where(
            DeliveryGrantModel.workspace_id == workspace_id,
            DeliveryGrantModel.installation_id == installation_id,
            DeliveryGrantModel.trigger_message_id == trigger_id,
        )
    )
    attempt = (
        await session.scalar(
            select(AgentSendAttemptModel).where(
                AgentSendAttemptModel.grant_id == grant.id
            )
        )
        if grant is not None
        else None
    )
    return grant, attempt


def _existing_reservation(
    turn: AgentTurnModel,
    input_sha256: str,
    grant: DeliveryGrantModel | None,
    attempt: AgentSendAttemptModel | None,
) -> DemoTurnReservation:
    terminal = _terminal_from_models(turn, grant, attempt)
    if terminal is not None:
        return DemoTurnReservation(
            DemoReservationState.TERMINAL,
            str(turn.id),
            input_sha256,
            terminal,
        )
    return DemoTurnReservation(
        DemoReservationState.RUNNING,
        str(turn.id),
        input_sha256,
    )


def _terminal_from_models(
    turn: AgentTurnModel,
    grant: DeliveryGrantModel | None,
    attempt: AgentSendAttemptModel | None,
) -> DemoTerminalReceipt | None:
    code = turn.result_code
    if not isinstance(code, str) or not code.startswith("mayak_demo_"):
        return None
    if attempt is not None and grant is not None and attempt.state in {
        "sent",
        "failed",
        "unknown",
        "blocked",
    }:
        delivery_state = DemoDeliveryState(attempt.state)
        _validate_terminal_delivery(grant, attempt, delivery_state)
        delivery = DemoDeliveryReceipt(
            delivery_ref=str(grant.id),
            state=delivery_state,
            provider_message_ref=attempt.provider_message_id,
            error_code=attempt.error_code,
        )
        disposition = {
            DemoDeliveryState.SENT: DemoDisposition.SENT,
            DemoDeliveryState.FAILED: DemoDisposition.FAILED,
            DemoDeliveryState.UNKNOWN: DemoDisposition.UNKNOWN,
            DemoDeliveryState.BLOCKED: DemoDisposition.BLOCKED,
        }[delivery_state]
        return DemoTerminalReceipt(disposition, code, delivery)
    if code == "mayak_demo_paused":
        return DemoTerminalReceipt(DemoDisposition.PAUSED, code)
    if code == "mayak_demo_human_takeover":
        return DemoTerminalReceipt(DemoDisposition.HUMAN_TAKEOVER, code)
    if code == "mayak_demo_installation_not_active":
        return DemoTerminalReceipt(DemoDisposition.BLOCKED, code)
    if turn.status == "failed":
        return DemoTerminalReceipt(DemoDisposition.FAILED, code)
    return None


def _installation_state(
    installation_state: str,
    session_state: str | None,
) -> DemoInstallationState:
    if session_state == "handoff_required":
        return DemoInstallationState.HUMAN_TAKEOVER
    if installation_state == "paused":
        return DemoInstallationState.PAUSED
    return DemoInstallationState.ACTIVE


def _stored_input_digest(context_message_ids: list[str] | None) -> str:
    for value in context_message_ids or ():
        if isinstance(value, str) and value.startswith(_INPUT_DIGEST_PREFIX):
            digest = value.removeprefix(_INPUT_DIGEST_PREFIX)
            _require_digest(digest, "stored_input_sha256")
            return digest
    raise UniversalMayakDemoError("mayak_demo_input_digest_missing")


def _scope_ids(scope: DemoScope) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return (
        _uuid(scope.workspace_ref, "workspace_ref"),
        _uuid(scope.installation_ref, "installation_ref"),
        _uuid(scope.dialog_ref, "dialog_ref"),
    )


def _uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise UniversalMayakDemoError(f"mayak_demo_{field}_invalid") from exc


def _event_uuid(value: str) -> uuid.UUID:
    prefix = "canonical_message_"
    if not value.startswith(prefix):
        raise UniversalMayakDemoError("mayak_demo_event_ref_invalid")
    return _uuid(value.removeprefix(prefix), "event_ref")


def _idempotency_uuid(value: str) -> uuid.UUID:
    prefix = "mayak_message_"
    if not value.startswith(prefix):
        raise UniversalMayakDemoError("mayak_demo_idempotency_key_invalid")
    return _uuid(value.removeprefix(prefix), "idempotency_key")


def _validate_terminal_delivery(
    grant: DeliveryGrantModel,
    attempt: AgentSendAttemptModel,
    state: DemoDeliveryState,
) -> None:
    if state is DemoDeliveryState.SENT:
        payload_valid = (
            isinstance(attempt.provider_message_id, str)
            and bool(attempt.provider_message_id)
            and attempt.error_code is None
        )
    else:
        payload_valid = (
            attempt.provider_message_id is None
            and isinstance(attempt.error_code, str)
            and bool(attempt.error_code)
        )
    grant_valid = (
        grant.revoked_at is not None and grant.used_at is None
        if state is DemoDeliveryState.BLOCKED
        else grant.used_at is not None and grant.revoked_at is None
    )
    if not payload_valid or not grant_valid:
        raise UniversalMayakDemoError("mayak_demo_delivery_receipt_invalid")


def _require_digest(value: str, field: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise UniversalMayakDemoError(f"mayak_demo_{field}_invalid")


def _stable_key(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return f"{prefix}:{digest}"
