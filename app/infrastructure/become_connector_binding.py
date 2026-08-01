"""Idempotent bootstrap for the isolated BECOME Telegram transport connector."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.canonical import TransportConnector, Workspace

_BECOME_CONNECTOR_KEY = "telegram_become_group"
_BECOME_CREDENTIAL_REF = "TELEGRAM_BECOME_BOT_TOKEN"


class BecomeConnectorBindingError(RuntimeError):
    """Fail closed when the isolated connector identity cannot be proven."""


@dataclass(frozen=True, slots=True)
class BecomeConnectorBindingResult:
    disposition: str
    connector_key: str


def become_connector_transition(
    *,
    connector_kind: str | None,
    credential_secret_ref: str | None,
    is_enabled: bool,
) -> str:
    """Classify one absent, exact or safely repairable connector state."""

    if connector_kind is None:
        if credential_secret_ref is not None or is_enabled:
            raise BecomeConnectorBindingError("become_connector_partial_identity")
        return "created"
    if connector_kind != "telegram_bot":
        raise BecomeConnectorBindingError("become_connector_kind_conflict")
    if credential_secret_ref not in {None, _BECOME_CREDENTIAL_REF}:
        raise BecomeConnectorBindingError("become_connector_credential_conflict")
    if credential_secret_ref == _BECOME_CREDENTIAL_REF and is_enabled:
        return "already_current"
    return "repaired"


async def reconcile_become_connector_binding(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> BecomeConnectorBindingResult | None:
    """Provision only the dedicated BECOME connector before worker startup."""

    if not settings.gate_become_connector_enabled:
        return None
    if not (
        settings.telegram_become_bot_token.get_secret_value()
        and settings.telegram_become_bot_username.strip()
        and settings.telegram_become_webhook_secret.get_secret_value()
    ):
        raise BecomeConnectorBindingError("become_credentials_incomplete")

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
            raise BecomeConnectorBindingError("become_workspace_not_found")

        connector = await session.scalar(
            select(TransportConnector)
            .where(
                TransportConnector.workspace_id == workspace.id,
                TransportConnector.connector_key == _BECOME_CONNECTOR_KEY,
            )
            .with_for_update()
        )
        disposition = become_connector_transition(
            connector_kind=connector.connector_kind if connector is not None else None,
            credential_secret_ref=(
                connector.credential_secret_ref if connector is not None else None
            ),
            is_enabled=connector.is_enabled if connector is not None else False,
        )
        if connector is None:
            connector = TransportConnector(
                workspace_id=workspace.id,
                connector_key=_BECOME_CONNECTOR_KEY,
                connector_kind="telegram_bot",
                credential_secret_ref=_BECOME_CREDENTIAL_REF,
                is_enabled=True,
            )
            session.add(connector)
        else:
            connector.credential_secret_ref = _BECOME_CREDENTIAL_REF
            connector.is_enabled = True
        await session.flush()
        return BecomeConnectorBindingResult(
            disposition=disposition,
            connector_key=_BECOME_CONNECTOR_KEY,
        )
