"""SQL binding admission for one owner-only Telegram Business Mayak."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.canonical_messaging import ConnectorKind, DialogKind
from app.application.universal_mayak_demo import (
    UNIVERSAL_MAYAK_PROMPT_SHA256,
    DemoScope,
)
from app.application.universal_mayak_private_ingress import (
    ApprovedPrivateBinding,
    ApprovedPrivateBindingResolver,
)
from app.application.work_ownership import AgentKey
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    InstructionRevisionModel,
)
from app.models.alpha import AgentInstallationModel
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TransportConnector,
    Workspace,
)
from app.models.domain import (
    Account,
    Message,
    Project,
    TelegramBusinessConnection,
)

MAYAK_WORKSPACE_KEY = "mark-personal"
MAYAK_REVISION_KEY = "universal-mayak-v1"
MAYAK_RUNTIME_PROFILE = "openai-responses-v1"


class SessionFactoryApprovedPrivateBindingResolver(
    ApprovedPrivateBindingResolver
):
    """Resolve exactly one active Oko installation; ambiguity fails closed."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def resolve(
        self,
        *,
        account_id: str,
        project_id: str,
        canonical_message_id: str,
        business_connection_id: str,
        provider_dialog_id: str,
        provider_actor_id: str,
    ) -> ApprovedPrivateBinding | None:
        account_uuid = _uuid_or_none(account_id)
        project_uuid = _uuid_or_none(project_id)
        message_uuid = _uuid_or_none(canonical_message_id)
        if (
            account_uuid is None
            or project_uuid is None
            or message_uuid is None
            or provider_actor_id != provider_dialog_id
        ):
            return None

        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        Workspace,
                        Account,
                        Project,
                        TelegramBusinessConnection,
                        TransportConnector,
                        Dialog,
                        CanonicalMessage,
                        Message,
                        AgentInstallationModel,
                        AgentDeploymentModel,
                        InstructionRevisionModel,
                        AgentDefinitionModel,
                    )
                    .join(
                        Account,
                        Account.id == account_uuid,
                    )
                    .join(
                        Project,
                        (Project.id == project_uuid)
                        & (Account.linked_project_id == Project.id),
                    )
                    .join(
                        TelegramBusinessConnection,
                        TelegramBusinessConnection.account_id == Account.id,
                    )
                    .join(
                        TransportConnector,
                        (TransportConnector.workspace_id == Workspace.id)
                        & (TransportConnector.connector_key == Account.alias),
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
                        Message,
                        (Message.canonical_message_id == CanonicalMessage.id)
                        & (Message.account_id == Account.id)
                        & (Message.project_id == Project.id),
                    )
                    .join(
                        AgentInstallationModel,
                        (AgentInstallationModel.workspace_id == Workspace.id)
                        & (
                            AgentInstallationModel.connector_id
                            == TransportConnector.id
                        )
                        & (AgentInstallationModel.dialog_id == Dialog.id),
                    )
                    .join(
                        AgentDeploymentModel,
                        (AgentDeploymentModel.workspace_id == Workspace.id)
                        & (
                            AgentDeploymentModel.id
                            == AgentInstallationModel.deployment_id
                        ),
                    )
                    .join(
                        InstructionRevisionModel,
                        (InstructionRevisionModel.workspace_id == Workspace.id)
                        & (
                            InstructionRevisionModel.id
                            == AgentInstallationModel.instruction_revision_id
                        )
                        & (
                            InstructionRevisionModel.id
                            == AgentDeploymentModel.instruction_revision_id
                        ),
                    )
                    .join(
                        AgentDefinitionModel,
                        (AgentDefinitionModel.workspace_id == Workspace.id)
                        & (
                            AgentDefinitionModel.id
                            == AgentDeploymentModel.agent_definition_id
                        ),
                    )
                    .where(
                        Workspace.key == MAYAK_WORKSPACE_KEY,
                        Workspace.is_active.is_(True),
                        Account.id == account_uuid,
                        Account.is_active.is_(True),
                        Account.read_enabled.is_(True),
                        Account.send_enabled.is_(True),
                        Project.id == project_uuid,
                        Project.is_active.is_(True),
                        TelegramBusinessConnection.business_connection_id
                        == business_connection_id,
                        TelegramBusinessConnection.is_enabled.is_(True),
                        TransportConnector.connector_kind
                        == ConnectorKind.TELEGRAM_BUSINESS.value,
                        TransportConnector.is_enabled.is_(True),
                        Dialog.provider_dialog_id == provider_dialog_id,
                        Dialog.dialog_kind == DialogKind.PRIVATE.value,
                        CanonicalMessage.id == message_uuid,
                        CanonicalMessage.direction == "inbound",
                        Message.direction == "inbound",
                        Message.business_connection_id
                        == business_connection_id,
                        Message.chat_id == provider_dialog_id,
                        Message.sender_id == provider_actor_id,
                        AgentDefinitionModel.agent_key == AgentKey.OKO.value,
                        AgentInstallationModel.state == "active",
                        AgentDeploymentModel.state == "active",
                        AgentDeploymentModel.runtime_profile
                        == MAYAK_RUNTIME_PROFILE,
                        InstructionRevisionModel.revision_key
                        == MAYAK_REVISION_KEY,
                        InstructionRevisionModel.validation_state
                        == "validated",
                    )
                )
            ).all()

        if len(rows) != 1:
            return None
        (
            workspace,
            account,
            project,
            connection,
            _connector,
            dialog,
            _canonical_message,
            _legacy_message,
            installation,
            _deployment,
            revision,
            _definition,
        ) = rows[0]
        if revision.content_sha256 != UNIVERSAL_MAYAK_PROMPT_SHA256:
            return None

        return ApprovedPrivateBinding(
            scope=DemoScope(
                workspace_ref=str(workspace.id),
                installation_ref=str(installation.id),
                dialog_ref=str(dialog.id),
            ),
            actor_scope="owner",
            active=True,
            account_id=str(account.id),
            project_id=str(project.id),
            business_connection_id=connection.business_connection_id,
            provider_dialog_id=dialog.provider_dialog_id,
            provider_actor_id=provider_actor_id,
        )


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (AttributeError, ValueError):
        return None
