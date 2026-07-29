"""Atomic cutover of one reviewed installation to the Beta transport identity."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.alpha import AgentInstallationModel
from app.models.canonical import Dialog, TransportConnector, Workspace

_ALPHA_CONNECTOR_KEY = "telegram_alpha_group"
_BETA_CONNECTOR_KEY = "telegram_beta_group"
_BETA_CREDENTIAL_REF = "TELEGRAM_BETA_BOT_TOKEN"
_ALLOWED_INSTALLATION_STATES = {"pending_approval", "active", "paused"}


class BetaConnectorBindingError(RuntimeError):
    """Fail closed when the reviewed installation cannot be moved exactly."""


@dataclass(frozen=True, slots=True)
class BetaConnectorBindingResult:
    disposition: str
    installation_key: str


def beta_binding_transition(
    *,
    current_connector_key: str,
    dialog_connector_key: str,
    foreign_beta_installations: int,
    foreign_beta_dialogs: int,
) -> str:
    """Validate one no-duplicate Alpha-to-Beta installation transition."""

    if foreign_beta_installations or foreign_beta_dialogs:
        raise BetaConnectorBindingError("beta_connector_binding_collision")
    if current_connector_key == _BETA_CONNECTOR_KEY:
        if dialog_connector_key != _BETA_CONNECTOR_KEY:
            raise BetaConnectorBindingError("beta_connector_dialog_mismatch")
        return "already_bound"
    if (
        current_connector_key != _ALPHA_CONNECTOR_KEY
        or dialog_connector_key != _ALPHA_CONNECTOR_KEY
    ):
        raise BetaConnectorBindingError("beta_connector_source_invalid")
    return "rebound"


async def reconcile_beta_connector_binding(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> BetaConnectorBindingResult | None:
    """Create the Beta connector and move only its pinned existing installation."""

    if not settings.gate_beta_connector_enabled:
        return None
    installation_key = settings.gate_beta_installation_key.strip()
    if not installation_key or len(installation_key) > 128:
        raise BetaConnectorBindingError("beta_installation_key_required")

    async with sessions.begin() as session:
        workspace = await session.scalar(
            select(Workspace)
            .where(
                Workspace.key == settings.gate_workspace_id,
                Workspace.is_active.is_(True),
            )
            .with_for_update()
        )
        if workspace is None:
            raise BetaConnectorBindingError("beta_workspace_not_found")

        beta_connector = await session.scalar(
            select(TransportConnector)
            .where(
                TransportConnector.workspace_id == workspace.id,
                TransportConnector.connector_key == _BETA_CONNECTOR_KEY,
            )
            .with_for_update()
        )
        if beta_connector is None:
            beta_connector = TransportConnector(
                workspace_id=workspace.id,
                connector_key=_BETA_CONNECTOR_KEY,
                connector_kind="telegram_bot",
                credential_secret_ref=_BETA_CREDENTIAL_REF,
                is_enabled=True,
            )
            session.add(beta_connector)
            await session.flush()
        elif (
            beta_connector.connector_kind != "telegram_bot"
            or beta_connector.credential_secret_ref
            not in {None, _BETA_CREDENTIAL_REF}
        ):
            raise BetaConnectorBindingError("beta_connector_identity_conflict")

        installation = await session.scalar(
            select(AgentInstallationModel)
            .where(
                AgentInstallationModel.workspace_id == workspace.id,
                AgentInstallationModel.installation_key == installation_key,
            )
            .with_for_update()
        )
        if installation is None:
            raise BetaConnectorBindingError("beta_installation_not_found")
        if installation.state not in _ALLOWED_INSTALLATION_STATES:
            raise BetaConnectorBindingError("beta_installation_state_invalid")

        dialog = await session.scalar(
            select(Dialog)
            .where(
                Dialog.workspace_id == workspace.id,
                Dialog.id == installation.dialog_id,
            )
            .with_for_update()
        )
        current_connector = await session.scalar(
            select(TransportConnector)
            .where(
                TransportConnector.workspace_id == workspace.id,
                TransportConnector.id == installation.connector_id,
            )
            .with_for_update()
        )
        if dialog is None or current_connector is None:
            raise BetaConnectorBindingError("beta_installation_scope_invalid")

        foreign_installations = int(
            await session.scalar(
                select(func.count())
                .select_from(AgentInstallationModel)
                .where(
                    AgentInstallationModel.workspace_id == workspace.id,
                    AgentInstallationModel.connector_id == beta_connector.id,
                    AgentInstallationModel.id != installation.id,
                    AgentInstallationModel.state != "revoked",
                )
            )
            or 0
        )
        foreign_dialogs = int(
            await session.scalar(
                select(func.count())
                .select_from(Dialog)
                .where(
                    Dialog.workspace_id == workspace.id,
                    Dialog.connector_id == beta_connector.id,
                    Dialog.provider_dialog_id == dialog.provider_dialog_id,
                    Dialog.id != dialog.id,
                )
            )
            or 0
        )
        dialog_connector = await session.scalar(
            select(TransportConnector).where(
                TransportConnector.workspace_id == workspace.id,
                TransportConnector.id == dialog.connector_id,
            )
        )
        if dialog_connector is None:
            raise BetaConnectorBindingError("beta_dialog_connector_missing")

        disposition = beta_binding_transition(
            current_connector_key=current_connector.connector_key,
            dialog_connector_key=dialog_connector.connector_key,
            foreign_beta_installations=foreign_installations,
            foreign_beta_dialogs=foreign_dialogs,
        )
        beta_connector.credential_secret_ref = _BETA_CREDENTIAL_REF
        beta_connector.is_enabled = True
        if disposition == "rebound":
            installation.connector_id = beta_connector.id
            dialog.connector_id = beta_connector.id
        await session.flush()
        return BetaConnectorBindingResult(
            disposition=disposition,
            installation_key=installation_key,
        )
