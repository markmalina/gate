"""PostgreSQL adapter for explicit BECOME controlled-consent receipts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_consent import (
    CONSENT_INTENT,
    QUEST_REF,
    BecomeConsentContext,
    PersistBecomeControlledConsentCommand,
    PersistedBecomeControlledConsent,
    consent_correlation_ref,
    consent_evidence,
)
from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
)
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.models.domain import TelegramReceipt


class SessionFactoryBecomeControlledConsentStore:
    """Resolve registered context and persist one explicit consent idempotently."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def resolve_context(
        self,
        *,
        workspace_key: str,
        provider_dialog_id: str,
        subject_ref: str,
    ) -> BecomeConsentContext | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        AgentInstallationModel.installation_key,
                        BecomeControlledIntakeReceiptModel.subject_ref,
                        BecomeControlledIntakeReceiptModel.receipt_digest,
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
                        (
                            BecomeControlledIntakeReceiptModel.workspace_id
                            == Workspace.id
                        )
                        & (
                            BecomeControlledIntakeReceiptModel.installation_id
                            == AgentInstallationModel.id
                        ),
                    )
                    .where(
                        Workspace.key == workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_key == BECOME_CONNECTOR_KEY,
                        TransportConnector.connector_kind == "telegram_bot",
                        TransportConnector.is_enabled.is_(True),
                        Dialog.provider_dialog_id == provider_dialog_id,
                        AgentInstallationModel.state == "active",
                        BecomeControlledIntakeReceiptModel.intent
                        == "registration_confirmed",
                        BecomeControlledIntakeReceiptModel.subject_ref == subject_ref,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            installation_ref, registered_subject_ref, registration_digest = row
            return BecomeConsentContext(
                installation_ref=str(installation_ref),
                subject_ref=str(registered_subject_ref),
                quest_ref=QUEST_REF,
                correlation_ref=consent_correlation_ref(
                    registration_receipt_digest=str(registration_digest),
                ),
            )

    async def persist(
        self,
        command: PersistBecomeControlledConsentCommand,
    ) -> PersistedBecomeControlledConsent:
        try:
            source_receipt_id = UUID(command.source_receipt_id)
        except ValueError as exc:
            raise BecomeControlledIntakeError(
                "become_consent_source_receipt_invalid"
            ) from exc

        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        Workspace,
                        Dialog,
                        AgentInstallationModel,
                        TelegramReceipt,
                        BecomeControlledIntakeReceiptModel,
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
                        TelegramReceipt,
                        TelegramReceipt.id == source_receipt_id,
                    )
                    .join(
                        BecomeControlledIntakeReceiptModel,
                        (
                            BecomeControlledIntakeReceiptModel.workspace_id
                            == Workspace.id
                        )
                        & (
                            BecomeControlledIntakeReceiptModel.installation_id
                            == AgentInstallationModel.id
                        ),
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
                        BecomeControlledIntakeReceiptModel.intent
                        == "registration_confirmed",
                        BecomeControlledIntakeReceiptModel.subject_ref
                        == command.receipt.subject_ref,
                    )
                    .with_for_update(of=AgentInstallationModel)
                )
            ).one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_consent_database_binding_invalid"
                )
            workspace, dialog, installation, source, registration = row
            evidence = consent_evidence(
                source.payload,
                invocation_command="become",
                bot_username="BecomeNowBot",
            )
            if evidence.provider_dialog_id != dialog.provider_dialog_id:
                raise BecomeControlledIntakeError(
                    "become_consent_dialog_mismatch"
                )
            if command.receipt.correlation_ref != consent_correlation_ref(
                registration_receipt_digest=registration.receipt_digest,
            ):
                raise BecomeControlledIntakeError(
                    "become_consent_registration_mismatch"
                )

            payload = command.receipt.payload()
            inserted = await session.scalar(
                insert(BecomeControlledConsentReceiptModel)
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
                    quest_ref=command.receipt.quest_ref,
                    correlation_ref=command.receipt.correlation_ref,
                    subject_ref=command.receipt.subject_ref,
                    action_ref=command.receipt.action_ref,
                    policy_version=command.receipt.policy_version,
                    policy_digest=command.receipt.policy_digest,
                    group_state=command.receipt.group_state,
                    member_count=command.receipt.member_count,
                    payload=payload,
                    accepted_at=command.receipt.accepted_at,
                )
                .on_conflict_do_nothing(
                    constraint="uq_become_consent_subject_policy",
                )
                .returning(BecomeControlledConsentReceiptModel.id)
            )
            if inserted is not None:
                return PersistedBecomeControlledConsent(
                    receipt_key=command.receipt.receipt_key,
                    receipt_digest=command.receipt.digest,
                    duplicate=False,
                )

            existing = await session.scalar(
                select(BecomeControlledConsentReceiptModel).where(
                    BecomeControlledConsentReceiptModel.installation_id
                    == installation.id,
                    BecomeControlledConsentReceiptModel.subject_ref
                    == command.receipt.subject_ref,
                    BecomeControlledConsentReceiptModel.policy_version
                    == command.receipt.policy_version,
                    BecomeControlledConsentReceiptModel.intent == CONSENT_INTENT,
                )
            )
            if existing is None:
                raise BecomeControlledIntakeError(
                    "become_consent_persistence_conflict"
                )
            return PersistedBecomeControlledConsent(
                receipt_key=existing.receipt_key,
                receipt_digest=existing.receipt_digest,
                duplicate=True,
            )
