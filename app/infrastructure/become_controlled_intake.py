"""PostgreSQL adapter for content-free BECOME controlled-intake receipts."""

from __future__ import annotations

from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
    PersistBecomeControlledIntakeCommand,
    PersistedBecomeControlledIntake,
    registration_evidence,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import BecomeControlledIntakeReceiptModel
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.models.domain import TelegramReceipt


class TelegramBecomeOwnerPresence:
    """Read only aggregate group size; never enumerate or expose participants."""

    def __init__(
        self,
        *,
        bot_token: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._bot_token = bot_token.strip()
        self._http_client = http_client

    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None:
        if (
            not self._bot_token
            or not installation_ref.startswith("installation-")
            or not provider_dialog_id
        ):
            return None
        endpoint = (
            f"https://api.telegram.org/bot{self._bot_token}/getChatMemberCount"
        )
        try:
            response = await self._http_client.post(
                endpoint,
                json={"chat_id": provider_dialog_id},
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        result = payload.get("result") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or not isinstance(result, int)
            or isinstance(result, bool)
            or result < 0
        ):
            return None
        return result


class SessionFactoryBecomeControlledIntakeStore:
    """Persist one exact receipt after rechecking its database authority."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def persist(
        self,
        command: PersistBecomeControlledIntakeCommand,
    ) -> PersistedBecomeControlledIntake:
        try:
            source_receipt_id = UUID(command.source_receipt_id)
        except ValueError as exc:
            raise BecomeControlledIntakeError(
                "become_registration_source_receipt_invalid"
            ) from exc

        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        Workspace,
                        TransportConnector,
                        Dialog,
                        AgentInstallationModel,
                        TelegramReceipt,
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
                        AgentInstallationModel,
                        (AgentInstallationModel.workspace_id == Workspace.id)
                        & (AgentInstallationModel.connector_id == TransportConnector.id)
                        & (AgentInstallationModel.dialog_id == Dialog.id),
                    )
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == source_receipt_id,
                    )
                    .where(
                        Workspace.key == command.workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_key == BECOME_CONNECTOR_KEY,
                        TransportConnector.connector_kind == "telegram_bot",
                        TransportConnector.is_enabled.is_(True),
                        Dialog.provider_dialog_id == command.provider_dialog_id,
                        AgentInstallationModel.installation_key
                        == command.receipt.installation_ref,
                        AgentInstallationModel.state == "active",
                        TelegramReceipt.bot_key == BECOME_CONNECTOR_KEY,
                    )
                    .with_for_update(of=AgentInstallationModel)
                )
            ).one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_registration_database_binding_invalid"
                )
            workspace, _connector, dialog, installation, source = row
            evidence = registration_evidence(
                source.payload,
                invocation_command="become",
                bot_username="BecomeNowBot",
            )
            if evidence.provider_dialog_id != dialog.provider_dialog_id:
                raise BecomeControlledIntakeError(
                    "become_registration_dialog_mismatch"
                )

            payload = command.receipt.payload()
            inserted = await session.scalar(
                insert(BecomeControlledIntakeReceiptModel)
                .values(
                    workspace_id=workspace.id,
                    installation_id=installation.id,
                    source_receipt_id=source.id,
                    receipt_key=command.receipt.receipt_key,
                    receipt_digest=command.receipt.digest,
                    schema_name=command.receipt.schema,
                    connector_key=command.receipt.connector_key,
                    intent=command.receipt.intent,
                    installation_ref=command.receipt.installation_ref,
                    subject_ref=command.receipt.subject_ref,
                    update_ref=command.receipt.update_ref,
                    group_state=command.receipt.group_state,
                    member_count=command.receipt.member_count,
                    payload=payload,
                    received_at=source.received_at,
                )
                .on_conflict_do_nothing(
                    index_elements=["source_receipt_id", "intent"],
                )
                .returning(BecomeControlledIntakeReceiptModel.id)
            )
            if inserted is not None:
                return PersistedBecomeControlledIntake(
                    receipt_key=command.receipt.receipt_key,
                    receipt_digest=command.receipt.digest,
                    duplicate=False,
                )

            existing = await session.scalar(
                select(BecomeControlledIntakeReceiptModel).where(
                    BecomeControlledIntakeReceiptModel.source_receipt_id == source.id,
                    BecomeControlledIntakeReceiptModel.intent
                    == command.receipt.intent,
                )
            )
            if (
                existing is None
                or existing.receipt_digest != command.receipt.digest
                or existing.payload != payload
            ):
                raise BecomeControlledIntakeError(
                    "become_registration_mutated_replay"
                )
            return PersistedBecomeControlledIntake(
                receipt_key=existing.receipt_key,
                receipt_digest=existing.receipt_digest,
                duplicate=True,
            )
