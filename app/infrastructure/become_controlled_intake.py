"""PostgreSQL adapter for content-free BECOME controlled-intake receipts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    CONTROLLED_INTAKE_SCHEMA,
    BecomeControlledIntakeError,
    PersistBecomeControlledIntakeCommand,
    PersistedBecomeControlledIntake,
    registration_evidence,
)
from app.infrastructure.become_media_intake import become_media_status
from app.infrastructure.become_media_prompt import (
    become_media_prompt_status,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
    BecomeRelationsConsentHandoffModel,
)
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)


class SessionFactoryBecomeRegistrationReplayer:
    """Requeue one previously processed registration when no intake receipt exists."""

    _SCAN_LIMIT = 16

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def requeue_latest(self, *, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("become_registration_replay_time_invalid")
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(BecomeControlledIntakeReceiptModel.id)
                .where(
                    BecomeControlledIntakeReceiptModel.connector_key
                    == BECOME_CONNECTOR_KEY,
                    BecomeControlledIntakeReceiptModel.intent
                    == "registration_confirmed",
                )
                .limit(1)
            )
            if existing is not None:
                return False

            result = await session.execute(
                select(TelegramOutboxEvent, TelegramReceipt)
                .select_from(TelegramReceipt)
                .join(
                    TelegramOutboxEvent,
                    TelegramOutboxEvent.receipt_id == TelegramReceipt.id,
                )
                .outerjoin(
                    BecomeControlledIntakeReceiptModel,
                    BecomeControlledIntakeReceiptModel.source_receipt_id
                    == TelegramReceipt.id,
                )
                .where(
                    TelegramReceipt.bot_key == BECOME_CONNECTOR_KEY,
                    TelegramReceipt.status == TelegramReceiptStatus.PROCESSED.value,
                    TelegramOutboxEvent.status
                    == TelegramOutboxStatus.DELIVERED.value,
                    BecomeControlledIntakeReceiptModel.id.is_(None),
                )
                .order_by(
                    TelegramReceipt.received_at.desc(),
                    TelegramReceipt.created_at.desc(),
                    TelegramReceipt.id.desc(),
                )
                .limit(self._SCAN_LIMIT)
                .with_for_update(of=TelegramOutboxEvent, skip_locked=True)
            )
            for event, receipt in result.all():
                try:
                    registration_evidence(
                        receipt.payload,
                        invocation_command="become",
                        bot_username="BecomeNowBot",
                    )
                except BecomeControlledIntakeError:
                    continue

                event.status = TelegramOutboxStatus.PENDING.value
                event.attempt_count = 0
                event.available_at = now
                event.locked_at = None
                event.delivered_at = None
                event.last_error_code = None
                event.last_error_at = None

                receipt.status = TelegramReceiptStatus.RECEIVED.value
                receipt.attempt_count = 0
                receipt.processing_started_at = None
                receipt.processed_at = None
                receipt.next_attempt_at = None
                receipt.last_error_code = None
                receipt.last_error_at = None
                return True
            return False


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


async def become_controlled_intake_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, object]:
    """Return aggregate receipt proof without content or provider identifiers."""

    row = (
        await session.execute(
            select(
                func.count(BecomeControlledIntakeReceiptModel.id),
                func.count(BecomeControlledIntakeReceiptModel.id).filter(
                    BecomeControlledIntakeReceiptModel.intent
                    == "registration_confirmed"
                ),
                func.count(BecomeControlledIntakeReceiptModel.id).filter(
                    BecomeControlledIntakeReceiptModel.group_state == "owner_only",
                    BecomeControlledIntakeReceiptModel.member_count == 2,
                ),
            )
            .join(
                Workspace,
                Workspace.id
                == BecomeControlledIntakeReceiptModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                BecomeControlledIntakeReceiptModel.connector_key
                == BECOME_CONNECTOR_KEY,
            )
        )
    ).one()
    total = int(row[0] or 0)
    registrations = int(row[1] or 0)
    owner_only = int(row[2] or 0)
    consent_count = int(
        (
            await session.execute(
                select(func.count(BecomeControlledConsentReceiptModel.id))
                .join(
                    Workspace,
                    Workspace.id
                    == BecomeControlledConsentReceiptModel.workspace_id,
                )
                .where(
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                    BecomeControlledConsentReceiptModel.connector_key
                    == BECOME_CONNECTOR_KEY,
                    BecomeControlledConsentReceiptModel.intent
                    == "consent_accepted",
                )
            )
        ).scalar_one()
        or 0
    )
    handoff_row = (
        await session.execute(
            select(
                func.count(BecomeRelationsConsentHandoffModel.id),
                func.count(BecomeRelationsConsentHandoffModel.id).filter(
                    BecomeRelationsConsentHandoffModel.status.in_(
                        ("pending_execution", "executed")
                    )
                ),
                func.count(BecomeRelationsConsentHandoffModel.id).filter(
                    BecomeRelationsConsentHandoffModel.status == "executed"
                ),
                func.coalesce(
                    func.sum(
                        BecomeRelationsConsentHandoffModel.attempt_count
                    ),
                    0,
                ),
            )
            .join(
                Workspace,
                Workspace.id
                == BecomeRelationsConsentHandoffModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                BecomeRelationsConsentHandoffModel.schema_name
                == "relations.controlled-consent-command@1",
            )
        )
    ).one()
    handoff_count = int(handoff_row[0] or 0)
    delivered_count = int(handoff_row[1] or 0)
    executed_count = int(handoff_row[2] or 0)
    delivery_attempts = int(handoff_row[3] or 0)
    relations_executed_ready = (
        consent_count == 1
        and handoff_count == 1
        and delivered_count == 1
        and executed_count == 1
    )
    media = await become_media_status(
        session,
        workspace_key=workspace_key,
    )
    media_pending = int(media["media_pending_download_count"])
    media_count = int(media["media_intake_count"])
    prompt = await become_media_prompt_status(
        session,
        workspace_key=workspace_key,
    )
    prompt_count = int(prompt["media_prompt_count"])
    prompt_sent = int(prompt["media_prompt_sent_count"])
    prompt_unknown = int(prompt["media_prompt_unknown_count"])
    prompt_failed = int(prompt["media_prompt_failed_count"])
    return {
        "schema": CONTROLLED_INTAKE_SCHEMA,
        "receipt_count": total,
        "registration_confirmed_count": registrations,
        "owner_only_count": owner_only,
        "registration_ready": (
            total == 1
            and registrations == 1
            and owner_only == 1
        ),
        "consent_accepted_count": consent_count,
        "consent_ready": consent_count == 1,
        "relations_handoff_count": handoff_count,
        "relations_handoff_ready": consent_count == 1 and handoff_count == 1,
        "relations_delivery_enabled": True,
        "relations_delivered_count": delivered_count,
        "relations_delivery_ready": delivered_count == 1,
        "relations_delivery_attempts": delivery_attempts,
        "relations_consent_executed_count": executed_count,
        "relations_consent_executed_ready": relations_executed_ready,
        "media_prompt_gate_ready": relations_executed_ready,
        "media_prompt_count": prompt_count,
        "media_prompt_sent_count": prompt_sent,
        "media_prompt_unknown_count": prompt_unknown,
        "media_prompt_failed_count": prompt_failed,
        "media_prompt_ready": prompt_sent == 1 or media_count == 1,
        "media_prompt_reply_required": (
            prompt_sent == 1 and media_count == 0
        ),
        "media_prompt_reconciliation_required": prompt_unknown > 0,
        "media_intake_gate_ready": relations_executed_ready,
        "media_intake_ready": (
            relations_executed_ready
            and media_pending == 0
        ),
        "media_intake_count": media_count,
        "media_pending_download_count": media_pending,
        "voice_intake_count": int(media["voice_intake_count"]),
        "video_intake_count": int(media["video_intake_count"]),
        "raw_media_retention_days": 7,
        "raw_media_retained_count": int(
            media["raw_media_retained_count"]
        ),
        "raw_media_purged_count": int(
            media["raw_media_purged_count"]
        ),
        "raw_media_retained_bytes": int(
            media["raw_media_retained_bytes"]
        ),
        "confirmed_transcript_retention_days": 30,
        "confirmed_transcript_retained_count": int(
            media["confirmed_transcript_retained_count"]
        ),
        "confirmed_transcript_purged_count": int(
            media["confirmed_transcript_purged_count"]
        ),
        "external_effects": media_count + prompt_sent,
        "provider_calls": 0,
        "messages_or_publications": prompt_sent,
        "media_bytes": int(media["raw_media_retained_bytes"]),
        "real_payments": 0,
        "private_content_exposed": False,
        "provider_identifiers_exposed": False,
        "telegram_identifiers_exposed": False,
    }
