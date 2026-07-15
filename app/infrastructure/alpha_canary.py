"""PostgreSQL reconciliation and grant issuance for the Alpha canary."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.alpha_canary import (
    AlphaCanaryError,
    AlphaCanaryStore,
    AlphaGrantResult,
    AlphaMembershipAction,
    AlphaMembershipResult,
    IssueAlphaDeliveryGrantCommand,
    ReconcileAlphaMembershipCommand,
)
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import (
    AlphaInstallationActionCommand,
    DiscoverAlphaInstallationCommand,
)
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
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
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace


class SessionFactoryAlphaCanaryStore(AlphaCanaryStore):
    """Derive membership and delivery authority from exact workspace DB bindings."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reconcile_membership(
        self, command: ReconcileAlphaMembershipCommand
    ) -> AlphaMembershipResult:
        async with self._sessions.begin() as session:
            repository = SqlAlphaLifecycleRepository(session)
            if command.action is AlphaMembershipAction.PRESENT:
                view = await repository.discover(
                    DiscoverAlphaInstallationCommand(
                        workspace_key=command.workspace_key,
                        connector_key=command.connector_key,
                        provider_dialog_id=command.provider_dialog_id,
                        dialog_kind=command.dialog_kind,
                        dialog_label=command.dialog_label,
                        bot_is_member=True,
                    )
                )
                return AlphaMembershipResult(
                    action=command.action,
                    installation_key=view.installation_key,
                    idempotent_replay=view.state
                    is not AlphaInstallationState.PENDING_APPROVAL,
                )

            installation_key = await session.scalar(
                select(AgentInstallationModel.installation_key)
                .join(
                    Workspace,
                    Workspace.id == AgentInstallationModel.workspace_id,
                )
                .join(
                    TransportConnector,
                    (TransportConnector.id == AgentInstallationModel.connector_id)
                    & (TransportConnector.workspace_id == AgentInstallationModel.workspace_id),
                )
                .join(
                    Dialog,
                    (Dialog.id == AgentInstallationModel.dialog_id)
                    & (Dialog.workspace_id == AgentInstallationModel.workspace_id),
                )
                .where(
                    Workspace.key == command.workspace_key,
                    TransportConnector.connector_key == command.connector_key,
                    Dialog.provider_dialog_id == command.provider_dialog_id,
                )
            )
            if installation_key is None:
                return AlphaMembershipResult(
                    action=command.action,
                    installation_key=None,
                    idempotent_replay=True,
                )
            view = await repository.revoke(
                AlphaInstallationActionCommand(
                    workspace_key=command.workspace_key,
                    installation_key=installation_key,
                )
            )
            return AlphaMembershipResult(
                action=command.action,
                installation_key=view.installation_key,
                idempotent_replay=view.state is AlphaInstallationState.REVOKED,
            )

    async def issue_delivery_grant(
        self, command: IssueAlphaDeliveryGrantCommand
    ) -> AlphaGrantResult:
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        Workspace,
                        TransportConnector,
                        Dialog,
                        CanonicalMessage,
                        AgentInstallationModel,
                        AgentSessionModel,
                        AgentTurnModel,
                    )
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
                        CanonicalMessage,
                        (CanonicalMessage.workspace_id == Workspace.id)
                        & (CanonicalMessage.dialog_id == Dialog.id),
                    )
                    .join(
                        AgentInstallationModel,
                        (AgentInstallationModel.workspace_id == Workspace.id)
                        & (AgentInstallationModel.connector_id == TransportConnector.id)
                        & (AgentInstallationModel.dialog_id == Dialog.id),
                    )
                    .join(
                        AgentSessionModel,
                        (AgentSessionModel.workspace_id == Workspace.id)
                        & (AgentSessionModel.dialog_id == Dialog.id)
                        & (
                            AgentSessionModel.deployment_id
                            == AgentInstallationModel.deployment_id
                        )
                        & (
                            AgentSessionModel.instruction_revision_id
                            == AgentInstallationModel.instruction_revision_id
                        ),
                    )
                    .join(
                        AgentDeploymentModel,
                        (AgentDeploymentModel.workspace_id == Workspace.id)
                        & (
                            AgentDeploymentModel.id
                            == AgentInstallationModel.deployment_id
                        )
                        & (
                            AgentDeploymentModel.instruction_revision_id
                            == AgentInstallationModel.instruction_revision_id
                        ),
                    )
                    .join(
                        InstructionRevisionModel,
                        (InstructionRevisionModel.workspace_id == Workspace.id)
                        & (
                            InstructionRevisionModel.id
                            == AgentInstallationModel.instruction_revision_id
                        ),
                    )
                    .join(
                        AgentTurnModel,
                        (AgentTurnModel.workspace_id == Workspace.id)
                        & (AgentTurnModel.session_id == AgentSessionModel.id)
                        & (AgentTurnModel.trigger_message_id == CanonicalMessage.id)
                        & (
                            AgentTurnModel.instruction_revision_id
                            == AgentInstallationModel.instruction_revision_id
                        ),
                    )
                    .where(
                        Workspace.key == command.workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_kind == "telegram_bot",
                        TransportConnector.is_enabled.is_(True),
                        Dialog.provider_dialog_id == command.provider_dialog_id,
                        CanonicalMessage.id == _uuid_text(command.trigger_message_id),
                        AgentInstallationModel.state
                        == AlphaInstallationState.ACTIVE.value,
                        AgentDeploymentModel.state == "active",
                        InstructionRevisionModel.validation_state == "validated",
                        AgentTurnModel.id == _turn_key_uuid(command.turn_key),
                        AgentTurnModel.status == "proposed",
                        AgentTurnModel.result_kind == "reply",
                    )
                    .with_for_update(of=AgentTurnModel)
                )
            ).one_or_none()
            if row is None:
                raise AlphaCanaryError("alpha_canary_grant_binding_invalid")
            (
                workspace,
                _connector,
                _dialog,
                trigger,
                installation,
                _agent_session,
                turn,
            ) = row
            candidate = turn.candidate_text
            if not isinstance(candidate, str) or not candidate.strip():
                raise AlphaCanaryError("alpha_canary_candidate_unavailable")
            delivered_text = (
                candidate
                if candidate.startswith(command.disclosure_prefix)
                else command.disclosure_prefix + candidate
            )
            if len(delivered_text) > 8_000:
                raise AlphaCanaryError("alpha_canary_candidate_too_long")
            candidate_sha = hashlib.sha256(delivered_text.encode("utf-8")).hexdigest()
            turn.candidate_text = delivered_text
            turn.candidate_sha256 = candidate_sha

            grant_key = _stable_grant_key(
                workspace.key,
                installation.installation_key,
                str(trigger.id),
                candidate_sha,
            )
            inserted = await session.scalar(
                insert(DeliveryGrantModel)
                .values(
                    workspace_id=workspace.id,
                    installation_id=installation.id,
                    trigger_message_id=trigger.id,
                    grant_key=grant_key,
                    candidate_sha256=candidate_sha,
                    expires_at=command.now + timedelta(minutes=5),
                    created_at=command.now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        DeliveryGrantModel.installation_id,
                        DeliveryGrantModel.trigger_message_id,
                        DeliveryGrantModel.candidate_sha256,
                    ]
                )
                .returning(DeliveryGrantModel.grant_key)
            )
            await session.flush()
            if inserted is not None:
                return AlphaGrantResult(grant_key=inserted)
            existing = await session.scalar(
                select(DeliveryGrantModel).where(
                    DeliveryGrantModel.workspace_id == workspace.id,
                    DeliveryGrantModel.installation_id == installation.id,
                    DeliveryGrantModel.trigger_message_id == trigger.id,
                    DeliveryGrantModel.candidate_sha256 == candidate_sha,
                )
            )
            if existing is None or existing.grant_key != grant_key:
                raise AlphaCanaryError("alpha_canary_grant_replay_conflict")
            return AlphaGrantResult(
                grant_key=existing.grant_key,
                idempotent_replay=True,
            )


async def alpha_canary_binding_count(
    session: AsyncSession,
    *,
    workspace_key: str,
    provider_dialog_id: str,
) -> int:
    """Count exact active/validated canary bindings without returning provider ids."""

    count = await session.scalar(
        select(func.count())
        .select_from(AgentInstallationModel)
        .join(Workspace, Workspace.id == AgentInstallationModel.workspace_id)
        .join(
            TransportConnector,
            (TransportConnector.id == AgentInstallationModel.connector_id)
            & (TransportConnector.workspace_id == AgentInstallationModel.workspace_id),
        )
        .join(
            Dialog,
            (Dialog.id == AgentInstallationModel.dialog_id)
            & (Dialog.workspace_id == AgentInstallationModel.workspace_id),
        )
        .join(
            AgentDeploymentModel,
            (AgentDeploymentModel.id == AgentInstallationModel.deployment_id)
            & (AgentDeploymentModel.workspace_id == AgentInstallationModel.workspace_id),
        )
        .join(
            InstructionRevisionModel,
            (InstructionRevisionModel.id == AgentInstallationModel.instruction_revision_id)
            & (InstructionRevisionModel.workspace_id == AgentInstallationModel.workspace_id)
            & (
                InstructionRevisionModel.id
                == AgentDeploymentModel.instruction_revision_id
            ),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key == "telegram_alpha_group",
            TransportConnector.connector_kind == "telegram_bot",
            TransportConnector.is_enabled.is_(True),
            Dialog.provider_dialog_id == provider_dialog_id,
            AgentInstallationModel.state == AlphaInstallationState.ACTIVE.value,
            AgentDeploymentModel.state == "active",
            InstructionRevisionModel.validation_state == "validated",
        )
    )
    return int(count or 0)


async def alpha_canary_audit_counts(
    session: AsyncSession,
    *,
    workspace_key: str,
    provider_dialog_id: str,
) -> dict[str, int]:
    """Return content-free durable evidence counts for the allowlisted group."""

    base = (
        select(Dialog.id)
        .join(Workspace, Workspace.id == Dialog.workspace_id)
        .join(
            TransportConnector,
            (TransportConnector.id == Dialog.connector_id)
            & (TransportConnector.workspace_id == Dialog.workspace_id),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key == "telegram_alpha_group",
            Dialog.provider_dialog_id == provider_dialog_id,
        )
    )
    dialog_ids = base.scalar_subquery()
    messages = await session.scalar(
        select(func.count())
        .select_from(CanonicalMessage)
        .where(CanonicalMessage.dialog_id == dialog_ids)
    )
    turns = await session.scalar(
        select(func.count())
        .select_from(AgentTurnModel)
        .join(AgentSessionModel, AgentSessionModel.id == AgentTurnModel.session_id)
        .where(AgentSessionModel.dialog_id == dialog_ids)
    )
    grants = await session.scalar(
        select(func.count())
        .select_from(DeliveryGrantModel)
        .join(
            AgentInstallationModel,
            AgentInstallationModel.id == DeliveryGrantModel.installation_id,
        )
        .where(AgentInstallationModel.dialog_id == dialog_ids)
    )
    attempts = await session.scalar(
        select(func.count())
        .select_from(AgentSendAttemptModel)
        .join(DeliveryGrantModel, DeliveryGrantModel.id == AgentSendAttemptModel.grant_id)
        .join(
            AgentInstallationModel,
            AgentInstallationModel.id == DeliveryGrantModel.installation_id,
        )
        .where(AgentInstallationModel.dialog_id == dialog_ids)
    )
    return {
        "canonical_messages": int(messages or 0),
        "agent_turns": int(turns or 0),
        "delivery_grants": int(grants or 0),
        "send_attempts": int(attempts or 0),
    }


def _stable_grant_key(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"alpha-grant-{digest[:64]}"


def _uuid_text(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AlphaCanaryError("alpha_canary_trigger_identity_invalid") from exc


def _turn_key_uuid(value: str) -> uuid.UUID:
    if not value.startswith("turn-"):
        raise AlphaCanaryError("alpha_canary_turn_identity_invalid")
    try:
        return uuid.UUID(value.removeprefix("turn-"))
    except ValueError as exc:
        raise AlphaCanaryError("alpha_canary_turn_identity_invalid") from exc
