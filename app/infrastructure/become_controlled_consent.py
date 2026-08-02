"""PostgreSQL adapter for explicit BECOME controlled-consent receipts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_consent import (
    CONSENT_INTENT,
    QUEST_REF,
    RELATIONS_CONSENT_COMMAND_SCHEMA,
    BecomeConsentContext,
    BecomeControlledConsentReceipt,
    PersistBecomeControlledConsentCommand,
    PersistedBecomeControlledConsent,
    consent_correlation_ref,
    consent_evidence,
    parse_controlled_consent_receipt,
    project_relations_consent_command,
)
from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
)
from app.application.become_relations_delivery import (
    parse_relations_consent_execution,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
    BecomeRelationsConsentHandoffModel,
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
                    accepted_at=datetime.fromisoformat(
                        command.receipt.accepted_at.replace("Z", "+00:00")
                    ),
                )
                .on_conflict_do_nothing(
                    constraint="uq_become_consent_subject_policy",
                )
                .returning(BecomeControlledConsentReceiptModel.id)
            )
            if inserted is not None:
                await _ensure_relations_handoff(
                    session,
                    workspace_id=workspace.id,
                    consent_receipt_id=inserted,
                    receipt=command.receipt,
                )
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
            if (
                existing.receipt_digest != command.receipt.digest
                or existing.payload != payload
            ):
                raise BecomeControlledIntakeError(
                    "become_consent_mutated_replay"
                )
            await _ensure_relations_handoff(
                session,
                workspace_id=workspace.id,
                consent_receipt_id=existing.id,
                receipt=parse_controlled_consent_receipt(existing.payload),
            )
            return PersistedBecomeControlledConsent(
                receipt_key=existing.receipt_key,
                receipt_digest=existing.receipt_digest,
                duplicate=True,
            )


async def _ensure_relations_handoff(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    consent_receipt_id: UUID,
    receipt: BecomeControlledConsentReceipt,
) -> bool:
    """Persist one exact Relations command without attempting delivery."""

    command = project_relations_consent_command(receipt)
    command_digest = str(command["command_digest"])
    inserted = await session.scalar(
        insert(BecomeRelationsConsentHandoffModel)
        .values(
            workspace_id=workspace_id,
            consent_receipt_id=consent_receipt_id,
            handoff_key=f"become_relations_{command_digest[:24]}",
            command_digest=command_digest,
            schema_name=RELATIONS_CONSENT_COMMAND_SCHEMA,
            status="pending_delivery",
            payload=command,
        )
        .on_conflict_do_nothing(
            constraint="uq_become_relations_handoff_consent",
        )
        .returning(BecomeRelationsConsentHandoffModel.id)
    )
    return inserted is not None


class SessionFactoryBecomeRelationsHandoffBackfiller:
    """Project one existing consent after a deployment adds the handoff table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_one(self) -> bool:
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        BecomeControlledConsentReceiptModel,
                        Workspace.id,
                    )
                    .join(
                        Workspace,
                        Workspace.id
                        == BecomeControlledConsentReceiptModel.workspace_id,
                    )
                    .outerjoin(
                        BecomeRelationsConsentHandoffModel,
                        BecomeRelationsConsentHandoffModel.consent_receipt_id
                        == BecomeControlledConsentReceiptModel.id,
                    )
                    .where(
                        Workspace.is_active.is_(True),
                        BecomeRelationsConsentHandoffModel.id.is_(None),
                    )
                    .order_by(
                        BecomeControlledConsentReceiptModel.created_at.asc(),
                        BecomeControlledConsentReceiptModel.id.asc(),
                    )
                    .limit(1)
                    .with_for_update(
                        of=BecomeControlledConsentReceiptModel,
                        skip_locked=True,
                    )
                )
            ).one_or_none()
            if row is None:
                return False
            consent, workspace_id = row
            return await _ensure_relations_handoff(
                session,
                workspace_id=workspace_id,
                consent_receipt_id=consent.id,
                receipt=parse_controlled_consent_receipt(consent.payload),
            )



BECOME_RELATIONS_CONSENT_ENDPOINT = (
    "http://become-relations-controlled.railway.internal:8080"
    "/internal/become/controlled-consent"
)


class SessionFactoryBecomeRelationsHandoffDeliverer:
    """Deliver one content-free handoff over Railway private networking."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        http_client: httpx.AsyncClient,
        endpoint: str = BECOME_RELATIONS_CONSENT_ENDPOINT,
    ) -> None:
        self._sessions = sessions
        self._http_client = http_client
        self._endpoint = endpoint

    async def deliver_one(self) -> bool:
        async with self._sessions.begin() as session:
            handoff = await session.scalar(
                select(BecomeRelationsConsentHandoffModel)
                .join(
                    Workspace,
                    Workspace.id
                    == BecomeRelationsConsentHandoffModel.workspace_id,
                )
                .where(
                    Workspace.is_active.is_(True),
                    BecomeRelationsConsentHandoffModel.status.in_(
                        ("pending_delivery", "pending_execution")
                    ),
                )
                .order_by(
                    BecomeRelationsConsentHandoffModel.created_at.asc(),
                    BecomeRelationsConsentHandoffModel.id.asc(),
                )
                .limit(1)
                .with_for_update(
                    of=BecomeRelationsConsentHandoffModel,
                    skip_locked=True,
                )
            )
            if handoff is None:
                return False

            handoff.attempt_count += 1
            try:
                response = await self._http_client.post(
                    self._endpoint,
                    json=handoff.payload,
                    timeout=10.0,
                )
                response.raise_for_status()
                raw_execution = response.json()
            except httpx.HTTPStatusError as exc:
                handoff.last_error_code = (
                    f"relations_http_{exc.response.status_code}"
                )
                return False
            except (httpx.HTTPError, ValueError):
                handoff.last_error_code = "relations_transport_or_json_error"
                return False

            expected_idempotency_key = handoff.payload.get("idempotency_key")
            if not isinstance(expected_idempotency_key, str):
                handoff.last_error_code = "relations_command_invalid"
                return False
            try:
                execution = parse_relations_consent_execution(
                    raw_execution,
                    expected_command_digest=handoff.command_digest,
                    expected_idempotency_key=expected_idempotency_key,
                )
            except BecomeControlledIntakeError:
                handoff.last_error_code = "relations_execution_invalid"
                return False

            handoff.status = "executed"
            handoff.execution_digest = str(execution["receipt_digest"])
            handoff.execution_payload = execution
            handoff.domain_event_id = str(execution["domain_event_id"])
            handoff.domain_event_digest = str(
                execution["domain_event_digest"]
            )
            handoff.last_error_code = None
            handoff.executed_at = datetime.now(UTC)
            return True
