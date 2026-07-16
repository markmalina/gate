"""Transactional PostgreSQL adapter for Alpha installation lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import (
    AlphaAccessReason,
    AlphaInstallationActionCommand,
    AlphaInstallationView,
    AlphaLifecycleError,
    AlphaUsageReservation,
    ApproveAlphaInstallationCommand,
    DiscoverAlphaInstallationCommand,
    RecordAlphaUsageCommand,
    ReserveAlphaUsageCommand,
)
from app.application.canonical_messaging import DialogKind
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    InstructionRevisionModel,
)
from app.models.alpha import (
    AgentInstallationModel,
    AlphaUsageRecordModel,
    DeliveryGrantModel,
)
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace


class SqlAlphaLifecycleRepository:
    """Keep discovery, approval, gating and accounting inside caller transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def discover(
        self, command: DiscoverAlphaInstallationCommand
    ) -> AlphaInstallationView:
        workspace_id = await self._workspace_id(command.workspace_key)
        connector = await self._connector(workspace_id, command.connector_key)
        if connector.connector_kind != "telegram_bot":
            raise AlphaLifecycleError(
                "alpha_connector_kind_invalid",
                "Alpha discovery requires the isolated Telegram bot connector",
            )

        dialog_id = await self._dialog_id(
            workspace_id=workspace_id,
            connector_id=connector.id,
            command=command,
        )
        installation_key = _stable_key(
            "installation",
            command.workspace_key,
            command.connector_key,
            command.provider_dialog_id,
        )
        await self._session.execute(
            insert(AgentInstallationModel)
            .values(
                workspace_id=workspace_id,
                connector_id=connector.id,
                dialog_id=dialog_id,
                deployment_id=None,
                instruction_revision_id=None,
                installation_key=installation_key,
                dialog_label=command.dialog_label,
                state=AlphaInstallationState.PENDING_APPROVAL.value,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    AgentInstallationModel.workspace_id,
                    AgentInstallationModel.connector_id,
                    AgentInstallationModel.dialog_id,
                ]
            )
        )
        await self._session.execute(
            update(AgentInstallationModel)
            .where(
                AgentInstallationModel.workspace_id == workspace_id,
                AgentInstallationModel.connector_id == connector.id,
                AgentInstallationModel.dialog_id == dialog_id,
            )
            .values(dialog_label=command.dialog_label, updated_at=func.now())
        )
        installation = await self._installation_by_dialog(
            workspace_id=workspace_id,
            connector_id=connector.id,
            dialog_id=dialog_id,
        )
        if installation.installation_key != installation_key:
            raise AlphaLifecycleError(
                "alpha_installation_identity_conflict",
                "The discovered group conflicts with an existing installation identity",
            )
        return await self._view(command.workspace_key, installation)

    async def approve(
        self, command: ApproveAlphaInstallationCommand
    ) -> AlphaInstallationView:
        workspace_id = await self._workspace_id(command.workspace_key)
        installation = await self._installation_by_key(
            workspace_id, command.installation_key, for_update=True
        )
        if (
            command.expected_state is not None
            and installation.state != command.expected_state.value
        ):
            raise AlphaLifecycleError(
                "alpha_installation_state_changed",
                "The installation state changed after the owner reviewed it",
            )
        deployment, revision = await self._approved_binding(
            workspace_id=workspace_id,
            deployment_key=command.deployment_key,
            revision_key=command.revision_key,
        )
        expected = (
            deployment.id,
            revision.id,
            command.max_turns_per_window,
            command.rate_window_seconds,
            command.max_tokens_per_turn,
        )
        current = (
            installation.deployment_id,
            installation.instruction_revision_id,
            installation.max_turns_per_window,
            installation.rate_window_seconds,
            installation.max_tokens_per_turn,
        )
        if installation.state == AlphaInstallationState.ACTIVE.value:
            if current != expected:
                raise AlphaLifecycleError(
                    "alpha_installation_approval_conflict",
                    "An active installation cannot be rebound by replay",
                )
            return await self._view(command.workspace_key, installation)
        if installation.state not in {
            AlphaInstallationState.DISCOVERED.value,
            AlphaInstallationState.PENDING_APPROVAL.value,
        }:
            raise AlphaLifecycleError(
                "alpha_installation_not_pending",
                "Only a discovered or pending installation can be approved",
            )

        installation.deployment_id = deployment.id
        installation.instruction_revision_id = revision.id
        installation.max_turns_per_window = command.max_turns_per_window
        installation.rate_window_seconds = command.rate_window_seconds
        installation.max_tokens_per_turn = command.max_tokens_per_turn
        installation.state = AlphaInstallationState.ACTIVE.value
        installation.updated_at = datetime.now(UTC)
        await self._session.flush()
        return await self._view(command.workspace_key, installation)

    async def pause(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView:
        workspace_id = await self._workspace_id(command.workspace_key)
        installation = await self._installation_by_key(
            workspace_id, command.installation_key, for_update=True
        )
        self._require_expected_state(installation, command)
        if installation.state == AlphaInstallationState.PAUSED.value:
            return await self._view(command.workspace_key, installation)
        if installation.state != AlphaInstallationState.ACTIVE.value:
            raise AlphaLifecycleError(
                "alpha_installation_not_active",
                "Only an active installation can be paused",
            )
        installation.state = AlphaInstallationState.PAUSED.value
        installation.updated_at = datetime.now(UTC)
        await self._revoke_unused_grants(workspace_id, installation.id)
        await self._session.flush()
        return await self._view(command.workspace_key, installation)

    async def resume(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView:
        workspace_id = await self._workspace_id(command.workspace_key)
        installation = await self._installation_by_key(
            workspace_id, command.installation_key, for_update=True
        )
        self._require_expected_state(installation, command)
        if installation.state == AlphaInstallationState.ACTIVE.value:
            return await self._view(command.workspace_key, installation)
        if installation.state != AlphaInstallationState.PAUSED.value:
            raise AlphaLifecycleError(
                "alpha_installation_not_paused",
                "Only a paused installation can be resumed",
            )
        if (
            installation.deployment_id is None
            or installation.instruction_revision_id is None
        ):
            raise AlphaLifecycleError(
                "alpha_installation_resume_binding_invalid",
                "A paused installation must retain an exact deployment binding",
            )
        connector = await self._session.get(
            TransportConnector,
            installation.connector_id,
        )
        dialog = await self._session.get(Dialog, installation.dialog_id)
        deployment = await self._session.get(
            AgentDeploymentModel,
            installation.deployment_id,
        )
        revision = await self._session.get(
            InstructionRevisionModel,
            installation.instruction_revision_id,
        )
        if (
            connector is None
            or connector.workspace_id != workspace_id
            or not connector.is_enabled
            or dialog is None
            or dialog.workspace_id != workspace_id
            or dialog.dialog_kind not in {
                DialogKind.GROUP.value,
                DialogKind.SUPERGROUP.value,
            }
            or deployment is None
            or deployment.workspace_id != workspace_id
            or deployment.state != "active"
            or revision is None
            or revision.workspace_id != workspace_id
            or revision.validation_state != "validated"
            or deployment.instruction_revision_id != revision.id
        ):
            raise AlphaLifecycleError(
                "alpha_installation_resume_binding_invalid",
                "The paused installation binding is no longer release-ready",
            )
        installation.state = AlphaInstallationState.ACTIVE.value
        installation.updated_at = datetime.now(UTC)
        await self._session.flush()
        return await self._view(command.workspace_key, installation)

    async def revoke(
        self, command: AlphaInstallationActionCommand
    ) -> AlphaInstallationView:
        workspace_id = await self._workspace_id(command.workspace_key)
        installation = await self._installation_by_key(
            workspace_id, command.installation_key, for_update=True
        )
        self._require_expected_state(installation, command)
        if installation.state != AlphaInstallationState.REVOKED.value:
            installation.state = AlphaInstallationState.REVOKED.value
            installation.updated_at = datetime.now(UTC)
            await self._revoke_unused_grants(workspace_id, installation.id)
            await self._session.flush()
        return await self._view(command.workspace_key, installation)

    async def list_status(self, workspace_key: str) -> tuple[AlphaInstallationView, ...]:
        workspace_id = await self._workspace_id(workspace_key)
        installations = (
            await self._session.execute(
                select(AgentInstallationModel)
                .where(AgentInstallationModel.workspace_id == workspace_id)
                .order_by(AgentInstallationModel.created_at, AgentInstallationModel.id)
            )
        ).scalars()
        return tuple([await self._view(workspace_key, item) for item in installations])

    async def reserve_usage(
        self, command: ReserveAlphaUsageCommand
    ) -> AlphaUsageReservation:
        workspace_id = await self._workspace_id(command.workspace_key)
        installation = await self._find_installation_by_key(
            workspace_id,
            command.installation_key,
            for_update=True,
        )
        if installation is None:
            return AlphaUsageReservation(False, AlphaAccessReason.UNKNOWN_INSTALLATION)
        connector = await self._session.get(TransportConnector, installation.connector_id)
        dialog = await self._session.get(Dialog, installation.dialog_id)
        if installation.state != AlphaInstallationState.ACTIVE.value:
            return AlphaUsageReservation(False, AlphaAccessReason.INSTALLATION_INACTIVE)
        if connector is None or connector.workspace_id != workspace_id or not connector.is_enabled:
            return AlphaUsageReservation(False, AlphaAccessReason.CONNECTOR_DISABLED)
        if dialog is None or dialog.dialog_kind not in {
            DialogKind.GROUP.value,
            DialogKind.SUPERGROUP.value,
        }:
            return AlphaUsageReservation(False, AlphaAccessReason.DIALOG_NOT_GROUP)
        if command.reserved_tokens > installation.max_tokens_per_turn:
            return AlphaUsageReservation(False, AlphaAccessReason.TOKEN_LIMIT_EXCEEDED)

        trigger_id = _uuid(command.trigger_message_id, "trigger_message_id")
        trigger_exists = await self._session.scalar(
            select(CanonicalMessage.id).where(
                CanonicalMessage.id == trigger_id,
                CanonicalMessage.workspace_id == workspace_id,
                CanonicalMessage.dialog_id == installation.dialog_id,
            )
        )
        if trigger_exists is None:
            return AlphaUsageReservation(False, AlphaAccessReason.TRIGGER_OUT_OF_SCOPE)

        existing = await self._session.scalar(
            select(AlphaUsageRecordModel).where(
                AlphaUsageRecordModel.installation_id == installation.id,
                AlphaUsageRecordModel.trigger_message_id == trigger_id,
            )
        )
        if existing is not None:
            if existing.reserved_tokens != command.reserved_tokens:
                raise AlphaLifecycleError(
                    "alpha_usage_replay_conflict",
                    "The trigger already has a different token reservation",
                )
            return AlphaUsageReservation(
                True,
                AlphaAccessReason.ALLOWED,
                usage_key=existing.usage_key,
                idempotent_replay=True,
            )

        if command.monthly_budget_microusd is not None:
            # Serialize the workspace-wide budget check across all approved Alpha groups.
            await self._session.execute(
                select(Workspace.id)
                .where(Workspace.id == workspace_id)
                .with_for_update()
            )
            month_start = command.now.astimezone(UTC).replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            reserved_month_tokens = await self._session.scalar(
                select(func.coalesce(func.sum(AlphaUsageRecordModel.reserved_tokens), 0))
                .where(
                    AlphaUsageRecordModel.workspace_id == workspace_id,
                    AlphaUsageRecordModel.state != "released",
                    AlphaUsageRecordModel.created_at >= month_start,
                )
            )
            projected_cost = (
                int(reserved_month_tokens or 0) + command.reserved_tokens
            ) * command.max_cost_microusd_per_token
            if projected_cost > command.monthly_budget_microusd:
                return AlphaUsageReservation(
                    False,
                    AlphaAccessReason.MONTHLY_BUDGET_EXCEEDED,
                )

        cutoff = command.now - timedelta(seconds=installation.rate_window_seconds)
        turns_in_window = await self._session.scalar(
            select(func.count())
            .select_from(AlphaUsageRecordModel)
            .where(
                AlphaUsageRecordModel.installation_id == installation.id,
                AlphaUsageRecordModel.state != "released",
                AlphaUsageRecordModel.created_at >= cutoff,
            )
        )
        if int(turns_in_window or 0) >= installation.max_turns_per_window:
            return AlphaUsageReservation(False, AlphaAccessReason.RATE_LIMITED)

        usage_key = _stable_key("usage", command.workspace_key, command.trigger_message_id)
        self._session.add(
            AlphaUsageRecordModel(
                workspace_id=workspace_id,
                installation_id=installation.id,
                trigger_message_id=trigger_id,
                usage_key=usage_key,
                state="reserved",
                reserved_tokens=command.reserved_tokens,
                created_at=command.now,
                updated_at=command.now,
            )
        )
        await self._session.flush()
        return AlphaUsageReservation(
            True,
            AlphaAccessReason.ALLOWED,
            usage_key=usage_key,
        )

    async def record_usage(self, command: RecordAlphaUsageCommand) -> None:
        workspace_id = await self._workspace_id(command.workspace_key)
        usage = await self._session.scalar(
            select(AlphaUsageRecordModel)
            .where(
                AlphaUsageRecordModel.workspace_id == workspace_id,
                AlphaUsageRecordModel.usage_key == command.usage_key,
            )
            .with_for_update()
        )
        if usage is None:
            raise AlphaLifecycleError("alpha_usage_not_found", "Usage reservation was not found")
        expected = (command.input_tokens, command.output_tokens)
        if command.input_tokens + command.output_tokens > usage.reserved_tokens:
            raise AlphaLifecycleError(
                "alpha_usage_exceeds_reservation",
                "Recorded token usage exceeds the approved reservation",
            )
        if usage.state == "recorded":
            if (usage.input_tokens, usage.output_tokens) != expected:
                raise AlphaLifecycleError(
                    "alpha_usage_record_conflict",
                    "Recorded token usage cannot be changed by replay",
                )
            return
        if usage.state != "reserved":
            raise AlphaLifecycleError(
                "alpha_usage_not_reserved",
                "Only a reserved usage record can be completed",
            )
        usage.input_tokens = command.input_tokens
        usage.output_tokens = command.output_tokens
        usage.state = "recorded"
        usage.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def _workspace_id(self, workspace_key: str) -> uuid.UUID:
        workspace_id = await self._session.scalar(
            select(Workspace.id).where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
            )
        )
        if workspace_id is None:
            raise AlphaLifecycleError(
                "alpha_workspace_not_found", "Active Alpha workspace was not found"
            )
        return workspace_id

    async def _connector(
        self, workspace_id: uuid.UUID, connector_key: str
    ) -> TransportConnector:
        connector = await self._session.scalar(
            select(TransportConnector).where(
                TransportConnector.workspace_id == workspace_id,
                TransportConnector.connector_key == connector_key,
            )
        )
        if connector is None:
            raise AlphaLifecycleError(
                "alpha_connector_not_found", "Alpha connector was not found in the workspace"
            )
        return connector

    async def _dialog_id(
        self,
        *,
        workspace_id: uuid.UUID,
        connector_id: uuid.UUID,
        command: DiscoverAlphaInstallationCommand,
    ) -> uuid.UUID:
        created = await self._session.scalar(
            insert(Dialog)
            .values(
                workspace_id=workspace_id,
                connector_id=connector_id,
                provider_dialog_id=command.provider_dialog_id,
                dialog_kind=command.dialog_kind.value,
            )
            .on_conflict_do_nothing(
                index_elements=[Dialog.workspace_id, Dialog.connector_id, Dialog.provider_dialog_id]
            )
            .returning(Dialog.id)
        )
        if created is not None:
            return created
        row = (
            await self._session.execute(
                select(Dialog.id, Dialog.dialog_kind).where(
                    Dialog.workspace_id == workspace_id,
                    Dialog.connector_id == connector_id,
                    Dialog.provider_dialog_id == command.provider_dialog_id,
                )
            )
        ).one_or_none()
        if row is None or row.dialog_kind != command.dialog_kind.value:
            raise AlphaLifecycleError(
                "alpha_dialog_identity_conflict",
                "The provider group identity conflicts with an existing dialog",
            )
        return cast(uuid.UUID, row[0])

    async def _installation_by_dialog(
        self, *, workspace_id: uuid.UUID, connector_id: uuid.UUID, dialog_id: uuid.UUID
    ) -> AgentInstallationModel:
        installation = await self._session.scalar(
            select(AgentInstallationModel).where(
                AgentInstallationModel.workspace_id == workspace_id,
                AgentInstallationModel.connector_id == connector_id,
                AgentInstallationModel.dialog_id == dialog_id,
            )
        )
        if installation is None:
            raise AlphaLifecycleError(
                "alpha_installation_not_found", "Alpha installation was not persisted"
            )
        return installation

    async def _installation_by_key(
        self,
        workspace_id: uuid.UUID,
        installation_key: str,
        *,
        for_update: bool,
    ) -> AgentInstallationModel:
        installation = await self._find_installation_by_key(
            workspace_id,
            installation_key,
            for_update=for_update,
        )
        if installation is None:
            raise AlphaLifecycleError(
                "alpha_installation_not_found", "Alpha installation was not found"
            )
        return installation

    async def _find_installation_by_key(
        self,
        workspace_id: uuid.UUID,
        installation_key: str,
        *,
        for_update: bool,
    ) -> AgentInstallationModel | None:
        statement = select(AgentInstallationModel).where(
            AgentInstallationModel.workspace_id == workspace_id,
            AgentInstallationModel.installation_key == installation_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(
            AgentInstallationModel | None,
            await self._session.scalar(statement),
        )

    async def _approved_binding(
        self, *, workspace_id: uuid.UUID, deployment_key: str, revision_key: str
    ) -> tuple[AgentDeploymentModel, InstructionRevisionModel]:
        row = (
            await self._session.execute(
                select(AgentDeploymentModel, InstructionRevisionModel)
                .join(
                    InstructionRevisionModel,
                    InstructionRevisionModel.id == AgentDeploymentModel.instruction_revision_id,
                )
                .join(
                    AgentDefinitionModel,
                    AgentDefinitionModel.id == AgentDeploymentModel.agent_definition_id,
                )
                .where(
                    AgentDeploymentModel.workspace_id == workspace_id,
                    AgentDeploymentModel.deployment_key == deployment_key,
                    AgentDeploymentModel.state == "active",
                    InstructionRevisionModel.workspace_id == workspace_id,
                    InstructionRevisionModel.revision_key == revision_key,
                    InstructionRevisionModel.validation_state == "validated",
                    AgentDefinitionModel.workspace_id == workspace_id,
                    AgentDefinitionModel.agent_key == "alpha_group",
                )
            )
        ).one_or_none()
        if row is None:
            raise AlphaLifecycleError(
                "alpha_binding_not_approved",
                "The active Alpha deployment and validated revision binding was not found",
            )
        return row[0], row[1]

    @staticmethod
    def _require_expected_state(
        installation: AgentInstallationModel,
        command: AlphaInstallationActionCommand,
    ) -> None:
        if (
            command.expected_state is not None
            and installation.state != command.expected_state.value
        ):
            raise AlphaLifecycleError(
                "alpha_installation_state_changed",
                "The installation state changed after the owner reviewed it",
            )

    async def _revoke_unused_grants(
        self, workspace_id: uuid.UUID, installation_id: uuid.UUID
    ) -> None:
        await self._session.execute(
            update(DeliveryGrantModel)
            .where(
                DeliveryGrantModel.workspace_id == workspace_id,
                DeliveryGrantModel.installation_id == installation_id,
                DeliveryGrantModel.used_at.is_(None),
                DeliveryGrantModel.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )

    async def _view(
        self, workspace_key: str, installation: AgentInstallationModel
    ) -> AlphaInstallationView:
        connector = await self._session.get(TransportConnector, installation.connector_id)
        dialog = await self._session.get(Dialog, installation.dialog_id)
        if connector is None or dialog is None:
            raise AlphaLifecycleError(
                "alpha_installation_scope_invalid",
                "The installation connector or dialog is unavailable",
            )
        deployment_key = None
        if installation.deployment_id is not None:
            deployment_key = await self._session.scalar(
                select(AgentDeploymentModel.deployment_key).where(
                    AgentDeploymentModel.id == installation.deployment_id,
                    AgentDeploymentModel.workspace_id == installation.workspace_id,
                )
            )
        revision_key = None
        if installation.instruction_revision_id is not None:
            revision_key = await self._session.scalar(
                select(InstructionRevisionModel.revision_key).where(
                    InstructionRevisionModel.id == installation.instruction_revision_id,
                    InstructionRevisionModel.workspace_id == installation.workspace_id,
                )
            )
        usage = (
            await self._session.execute(
                select(
                    func.count(AlphaUsageRecordModel.id),
                    func.coalesce(func.sum(AlphaUsageRecordModel.input_tokens), 0),
                    func.coalesce(func.sum(AlphaUsageRecordModel.output_tokens), 0),
                ).where(
                    AlphaUsageRecordModel.installation_id == installation.id,
                    AlphaUsageRecordModel.state != "released",
                )
            )
        ).one()
        return AlphaInstallationView(
            installation_key=installation.installation_key,
            workspace_key=workspace_key,
            connector_key=connector.connector_key,
            connector_enabled=connector.is_enabled,
            dialog_kind=DialogKind(dialog.dialog_kind),
            dialog_label=installation.dialog_label,
            state=AlphaInstallationState(installation.state),
            deployment_key=deployment_key,
            revision_key=revision_key,
            max_turns_per_window=installation.max_turns_per_window,
            rate_window_seconds=installation.rate_window_seconds,
            max_tokens_per_turn=installation.max_tokens_per_turn,
            reserved_turns=int(usage[0] or 0),
            recorded_tokens=int(usage[1] or 0) + int(usage[2] or 0),
        )


def _stable_key(prefix: str, *parts: str) -> str:
    identity = ":".join(parts)
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


def _uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AlphaLifecycleError(
            f"alpha_{field_name}_invalid", f"{field_name} must be a UUID"
        ) from exc
