"""PostgreSQL reconciliation and grant issuance for the Alpha canary."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Collection
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
    compose_alpha_delivery_text,
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
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TransportConnector,
    Workspace,
)

MAX_ALPHA_GROUP_CAPACITY = 10_000


class SessionFactoryAlphaCanaryStore(AlphaCanaryStore):
    """Derive membership and delivery authority from exact workspace DB bindings."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        group_capacity: int = 1,
        pinned_installation_key: str | None = None,
    ) -> None:
        if not 1 <= group_capacity <= MAX_ALPHA_GROUP_CAPACITY:
            raise ValueError(
                "group_capacity must be between 1 and "
                f"{MAX_ALPHA_GROUP_CAPACITY}"
            )
        if pinned_installation_key is not None:
            pinned_installation_key = pinned_installation_key.strip()
            if not pinned_installation_key or len(pinned_installation_key) > 128:
                raise ValueError("pinned_installation_key must be a bounded token")
        self._sessions = sessions
        self._group_capacity = group_capacity
        self._pinned_installation_key = pinned_installation_key

    async def reconcile_membership(
        self, command: ReconcileAlphaMembershipCommand
    ) -> AlphaMembershipResult:
        async with self._sessions.begin() as session:
            repository = SqlAlphaLifecycleRepository(session)
            if command.action is AlphaMembershipAction.PRESENT:
                connector_id = await session.scalar(
                    select(TransportConnector.id)
                    .join(Workspace, Workspace.id == TransportConnector.workspace_id)
                    .where(
                        Workspace.key == command.workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_key == command.connector_key,
                        TransportConnector.connector_kind == "telegram_bot",
                        TransportConnector.is_enabled.is_(True),
                    )
                    .with_for_update(of=TransportConnector)
                )
                if connector_id is None:
                    raise AlphaCanaryError("alpha_canary_connector_unavailable")
                if self._pinned_installation_key is not None:
                    pinned_key = await session.scalar(
                        select(AgentInstallationModel.installation_key)
                        .join(
                            Dialog,
                            (Dialog.id == AgentInstallationModel.dialog_id)
                            & (
                                Dialog.workspace_id
                                == AgentInstallationModel.workspace_id
                            ),
                        )
                        .where(
                            AgentInstallationModel.workspace_id
                            == Dialog.workspace_id,
                            AgentInstallationModel.connector_id == connector_id,
                            AgentInstallationModel.installation_key
                            == self._pinned_installation_key,
                            AgentInstallationModel.state
                            != AlphaInstallationState.REVOKED.value,
                            Dialog.connector_id == connector_id,
                            Dialog.provider_dialog_id
                            == command.provider_dialog_id,
                            Dialog.dialog_kind == command.dialog_kind.value,
                        )
                    )
                    if pinned_key != self._pinned_installation_key:
                        raise AlphaCanaryError(
                            "alpha_canary_pinned_installation_mismatch"
                        )
                    return AlphaMembershipResult(
                        action=command.action,
                        installation_key=pinned_key,
                        idempotent_replay=True,
                    )
                pinned_dialog_ids = (
                    await session.scalars(
                        select(Dialog.provider_dialog_id)
                        .join(
                            AgentInstallationModel,
                            (AgentInstallationModel.dialog_id == Dialog.id)
                            & (
                                AgentInstallationModel.workspace_id
                                == Dialog.workspace_id
                            ),
                        )
                        .join(
                            Workspace,
                            Workspace.id == AgentInstallationModel.workspace_id,
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
                        .where(
                            Workspace.key == command.workspace_key,
                            TransportConnector.id == connector_id,
                        )
                    )
                ).all()
                if not alpha_group_capacity_available(
                    existing_provider_dialog_ids=pinned_dialog_ids,
                    provider_dialog_id=command.provider_dialog_id,
                    capacity=self._group_capacity,
                ):
                    raise AlphaCanaryError("alpha_canary_group_capacity_reached")
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
            delivered_text = compose_alpha_delivery_text(
                candidate=candidate,
                disclosure_prefix=command.disclosure_prefix,
            )
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

    async def is_active_group(
        self,
        *,
        workspace_key: str,
        connector_key: str,
        provider_dialog_id: str,
    ) -> bool:
        async with self._sessions() as session:
            count = await _alpha_canary_binding_count(
                session,
                workspace_key=workspace_key,
                connector_key=connector_key,
                provider_dialog_id=provider_dialog_id,
            )
            return count == 1


async def alpha_canary_binding_count(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> int:
    """Count exact active/validated canary bindings without returning provider ids."""

    return await _alpha_canary_binding_count(
        session,
        workspace_key=workspace_key,
        connector_key="telegram_alpha_group",
        provider_dialog_id=None,
    )


async def _alpha_canary_binding_count(
    session: AsyncSession,
    *,
    workspace_key: str,
    connector_key: str,
    provider_dialog_id: str | None,
) -> int:
    statement = (
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
            TransportConnector.connector_key == connector_key,
            TransportConnector.connector_kind == "telegram_bot",
            TransportConnector.is_enabled.is_(True),
            AgentInstallationModel.state == AlphaInstallationState.ACTIVE.value,
            AgentDeploymentModel.state == "active",
            InstructionRevisionModel.validation_state == "validated",
        )
    )
    if provider_dialog_id is not None:
        statement = statement.where(Dialog.provider_dialog_id == provider_dialog_id)
    count = await session.scalar(statement)
    return int(count or 0)


async def alpha_canary_active_installation_count(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> int:
    """Count active Alpha group installations without assuming exact bindings."""

    count = await session.scalar(
        select(func.count())
        .select_from(AgentInstallationModel)
        .join(Workspace, Workspace.id == AgentInstallationModel.workspace_id)
        .join(
            TransportConnector,
            (TransportConnector.id == AgentInstallationModel.connector_id)
            & (TransportConnector.workspace_id == AgentInstallationModel.workspace_id),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key == "telegram_alpha_group",
            TransportConnector.connector_kind == "telegram_bot",
            AgentInstallationModel.state == AlphaInstallationState.ACTIVE.value,
        )
    )
    return int(count or 0)


async def alpha_canary_installation_count(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(AgentInstallationModel)
        .join(Workspace, Workspace.id == AgentInstallationModel.workspace_id)
        .join(
            TransportConnector,
            (TransportConnector.id == AgentInstallationModel.connector_id)
            & (TransportConnector.workspace_id == AgentInstallationModel.workspace_id),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key == "telegram_alpha_group",
            TransportConnector.connector_kind == "telegram_bot",
        )
    )
    return int(count or 0)


async def alpha_canary_audit_counts(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, int]:
    """Return content-free durable evidence counts for the pinned group."""

    base = (
        select(Dialog.id)
        .join(
            AgentInstallationModel,
            (AgentInstallationModel.dialog_id == Dialog.id)
            & (AgentInstallationModel.workspace_id == Dialog.workspace_id),
        )
        .join(Workspace, Workspace.id == Dialog.workspace_id)
        .join(
            TransportConnector,
            (TransportConnector.id == AgentInstallationModel.connector_id)
            & (TransportConnector.workspace_id == Dialog.workspace_id),
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key == "telegram_alpha_group",
            TransportConnector.connector_kind == "telegram_bot",
        )
    )
    messages = await session.scalar(
        select(func.count())
        .select_from(CanonicalMessage)
        .where(CanonicalMessage.dialog_id.in_(base))
    )
    turns = await session.scalar(
        select(func.count())
        .select_from(AgentTurnModel)
        .join(AgentSessionModel, AgentSessionModel.id == AgentTurnModel.session_id)
        .where(AgentSessionModel.dialog_id.in_(base))
    )
    grants = await session.scalar(
        select(func.count())
        .select_from(DeliveryGrantModel)
        .join(
            AgentInstallationModel,
            AgentInstallationModel.id == DeliveryGrantModel.installation_id,
        )
        .where(AgentInstallationModel.dialog_id.in_(base))
    )
    attempts = await session.scalar(
        select(func.count())
        .select_from(AgentSendAttemptModel)
        .join(DeliveryGrantModel, DeliveryGrantModel.id == AgentSendAttemptModel.grant_id)
        .join(
            AgentInstallationModel,
            AgentInstallationModel.id == DeliveryGrantModel.installation_id,
        )
        .where(AgentInstallationModel.dialog_id.in_(base))
    )
    return {
        "canonical_messages": int(messages or 0),
        "agent_turns": int(turns or 0),
        "delivery_grants": int(grants or 0),
        "send_attempts": int(attempts or 0),
    }


def alpha_group_capacity_available(
    *,
    existing_provider_dialog_ids: Collection[str],
    provider_dialog_id: str,
    capacity: int,
) -> bool:
    """Allow idempotent replay or one new group inside the configured bound."""

    if not 1 <= capacity <= MAX_ALPHA_GROUP_CAPACITY:
        raise ValueError(
            f"capacity must be between 1 and {MAX_ALPHA_GROUP_CAPACITY}"
        )
    return (
        provider_dialog_id in existing_provider_dialog_ids
        or len(existing_provider_dialog_ids) < capacity
    )


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
