"""PostgreSQL isolation and idempotency adapter for Beta Space intake."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from app.application.beta_space_intake import (
    CONSENT_RESPONSE,
    BetaConsentDeliveryJob,
    BetaSpaceBinding,
    BetaSpaceReservation,
    BetaSpaceSource,
)
from app.core.config import Settings
from app.models.beta_space import (
    BetaCheckpointDispatchIntentModel,
    BetaConsentDeliveryModel,
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
    BetaSpaceSearchDocumentModel,
    BetaSpaceWorkItemModel,
)
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import TelegramReceipt
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BETA_SPACE_REF = "spc_32443e2deeeaf0bd6ff0012c1e3b39e1"
BETA_TENANT_REF = "beta-user-katya-001"
BETA_INSTALLATION_REF = "installation-99feb906e18094b42dccf9f3636dcfcf"
BETA_STORAGE_REF = "beta-group-katya-dental-001"
BETA_CONNECTOR_KEY = "telegram_beta_group"


class BetaSpaceConfigurationError(RuntimeError):
    """Reject partial or conflicting production binding configuration."""


@dataclass(frozen=True, slots=True)
class BetaSpaceBindingReconciliation:
    disposition: str
    space_ref: str


def beta_space_production_ready(settings: Settings) -> bool:
    """Require every independent production and Telegram access gate."""

    return all(
        (
            settings.gate_env == "production",
            settings.gate_beta_connector_enabled,
            settings.gate_beta_privacy_mode_confirmed,
        )
    )


async def beta_katya_space_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    settings: Settings,
) -> dict[str, object]:
    """Return a redacted activation snapshot without Telegram identifiers or payloads."""

    workspace_id = await session.scalar(
        select(Workspace.id).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    binding = None
    event_count = 0
    consent_count = 0
    fact_count = 0
    delivery_sent_count = 0
    delivery_unknown_count = 0
    if workspace_id is not None:
        binding = await session.scalar(
            select(BetaSpaceBindingModel).where(
                BetaSpaceBindingModel.workspace_id == workspace_id,
                BetaSpaceBindingModel.space_ref == BETA_SPACE_REF,
            )
        )
        if binding is not None:
            event_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                    )
                )
                or 0
            )
            consent_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                        BetaSpaceEventModel.event_kind == "consent",
                    )
                )
                or 0
            )
            fact_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                        BetaSpaceEventModel.event_kind == "fact",
                    )
                )
                or 0
            )
            delivery_sent_count = int(
                await session.scalar(
                    select(func.count(BetaConsentDeliveryModel.id)).where(
                        BetaConsentDeliveryModel.workspace_id == workspace_id,
                        BetaConsentDeliveryModel.binding_id == binding.id,
                        BetaConsentDeliveryModel.state == "sent",
                    )
                )
                or 0
            )
            delivery_unknown_count = int(
                await session.scalar(
                    select(func.count(BetaConsentDeliveryModel.id)).where(
                        BetaConsentDeliveryModel.workspace_id == workspace_id,
                        BetaConsentDeliveryModel.binding_id == binding.id,
                        BetaConsentDeliveryModel.state == "unknown",
                    )
                )
                or 0
            )

    binding_state = binding.state if binding is not None else "absent"
    binding_live = binding_state in {"ready", "consented"}
    configured = {
        "chat_binding": binding is not None,
        "subject_binding": binding is not None,
        "consent_digest": binding is not None,
    }
    gates = {
        "production": settings.gate_env == "production",
        "connector": settings.gate_beta_connector_enabled,
        "privacy_mode_confirmed": settings.gate_beta_privacy_mode_confirmed,
        "space_enabled": binding_live,
        "space_ready": binding_live,
        "delivery_enabled": binding_live,
    }
    if not beta_space_production_ready(settings):
        state = "connector_not_ready"
    elif binding is None:
        state = "awaiting_binding"
    elif binding_state == "ready":
        state = "awaiting_consent"
    elif binding_state == "consented" and delivery_unknown_count:
        state = "reconciliation_required"
    elif binding_state == "consented":
        state = "active"
    else:
        state = "revoked"

    return {
        "state": state,
        "gates": gates,
        "configured": configured,
        "binding_state": binding_state,
        "production_ready": beta_space_production_ready(settings) and binding_live,
        "event_count": event_count,
        "consent_count": consent_count,
        "fact_count": fact_count,
        "delivery_sent_count": delivery_sent_count,
        "delivery_unknown_count": delivery_unknown_count,
        "private_identifiers_exposed": False,
        "private_payload_exposed": False,
    }


async def reconcile_beta_katya_space_binding(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> BetaSpaceBindingReconciliation | None:
    """Create one exact secret-backed binding without touching legacy installations."""

    if not settings.gate_beta_katya_space_enabled:
        return None
    if settings.gate_env != "production" or not settings.gate_beta_katya_space_ready:
        raise BetaSpaceConfigurationError("beta_space_not_production_ready")
    chat_id = settings.gate_beta_katya_chat_id.get_secret_value().strip()
    user_id = settings.gate_beta_katya_user_id.get_secret_value().strip()
    consent_sha = settings.gate_beta_katya_consent_sha256.get_secret_value().strip().lower()
    if (
        not _telegram_id(chat_id)
        or not _telegram_id(user_id, positive=True)
        or len(consent_sha) != 64
        or any(value not in "0123456789abcdef" for value in consent_sha)
    ):
        raise BetaSpaceConfigurationError("beta_space_secret_binding_invalid")
    observed_at = now or datetime.now(UTC)
    async with sessions() as session, session.begin():
        workspace = await session.scalar(
            select(Workspace).where(
                Workspace.key == settings.gate_workspace_id,
                Workspace.is_active.is_(True),
            )
        )
        if workspace is None:
            raise BetaSpaceConfigurationError("beta_space_workspace_missing")
        connector = await session.scalar(
            select(TransportConnector).where(
                TransportConnector.workspace_id == workspace.id,
                TransportConnector.connector_key == BETA_CONNECTOR_KEY,
                TransportConnector.is_enabled.is_(True),
            )
        )
        if connector is None:
            raise BetaSpaceConfigurationError("beta_space_connector_missing")
        existing = await session.scalar(
            select(BetaSpaceBindingModel)
            .where(
                BetaSpaceBindingModel.workspace_id == workspace.id,
                BetaSpaceBindingModel.space_ref == BETA_SPACE_REF,
            )
            .with_for_update()
        )
        expected = (
            connector.id,
            BETA_TENANT_REF,
            BETA_INSTALLATION_REF,
            BETA_STORAGE_REF,
            chat_id,
            user_id,
            consent_sha,
        )
        if existing is not None:
            actual = (
                existing.connector_id,
                existing.tenant_ref,
                existing.installation_ref,
                existing.storage_ref,
                existing.telegram_chat_id,
                existing.subject_telegram_user_id,
                existing.consent_sha256,
            )
            if actual != expected or existing.state == "revoked":
                raise BetaSpaceConfigurationError("beta_space_binding_conflict")
            return BetaSpaceBindingReconciliation("already_bound", BETA_SPACE_REF)
        session.add(
            BetaSpaceBindingModel(
                workspace_id=workspace.id,
                connector_id=connector.id,
                tenant_ref=BETA_TENANT_REF,
                space_ref=BETA_SPACE_REF,
                installation_ref=BETA_INSTALLATION_REF,
                storage_ref=BETA_STORAGE_REF,
                telegram_chat_id=chat_id,
                subject_telegram_user_id=user_id,
                consent_sha256=consent_sha,
                actor_policy="subject_only",
                provenance_state="not_applicable",
                provenance_evidence_kind="not_applicable",
                state="ready",
                ready_at=observed_at,
            )
        )
    return BetaSpaceBindingReconciliation("created", BETA_SPACE_REF)


async def enqueue_beta_checkpoint_dispatch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    binding_id: uuid.UUID,
    space_id: str,
    accepted_sequence: int,
    now: datetime,
) -> None:
    """Append one durable wake generation without inventing a Space sequence."""

    excluded = insert(BetaCheckpointDispatchIntentModel).excluded
    await session.execute(
        insert(BetaCheckpointDispatchIntentModel)
        .values(
            workspace_id=workspace_id,
            binding_id=binding_id,
            space_id=space_id,
            accepted_sequence=accepted_sequence,
            dispatched_sequence=0,
            wake_generation=1,
            dispatched_wake_generation=0,
            state="pending",
            attempt_count=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                BetaCheckpointDispatchIntentModel.workspace_id,
                BetaCheckpointDispatchIntentModel.binding_id,
            ],
            set_={
                "accepted_sequence": func.greatest(
                    BetaCheckpointDispatchIntentModel.accepted_sequence,
                    excluded.accepted_sequence,
                ),
                "wake_generation": (
                    BetaCheckpointDispatchIntentModel.wake_generation + 1
                ),
                "state": case(
                    (
                        BetaCheckpointDispatchIntentModel.state == "sending",
                        "sending",
                    ),
                    else_="pending",
                ),
                "last_error_code": None,
                "next_attempt_at": func.least(
                    BetaCheckpointDispatchIntentModel.next_attempt_at,
                    excluded.next_attempt_at,
                ),
                "settled_at": None,
                "updated_at": excluded.updated_at,
            },
        )
    )


class SqlBetaSpaceStore:
    """Exact-scope store; no operation supports an unscoped scan or fallback."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_binding(
        self,
        *,
        workspace_key: str,
        connector_key: str,
        telegram_chat_id: str,
    ) -> BetaSpaceBinding | None:
        row = await self._session.scalar(
            select(BetaSpaceBindingModel)
            .join(
                Workspace,
                Workspace.id == BetaSpaceBindingModel.workspace_id,
            )
            .join(
                TransportConnector,
                (TransportConnector.id == BetaSpaceBindingModel.connector_id)
                & (
                    TransportConnector.workspace_id
                    == BetaSpaceBindingModel.workspace_id
                ),
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                TransportConnector.connector_key == connector_key,
                TransportConnector.is_enabled.is_(True),
                BetaSpaceBindingModel.telegram_chat_id == telegram_chat_id,
                BetaSpaceBindingModel.state.in_(("ready", "consented")),
            )
        )
        return _binding(row) if row is not None else None

    async def reserve_consent(
        self, *, binding: BetaSpaceBinding, source: BetaSpaceSource
    ) -> BetaSpaceReservation:
        row = await self._locked_binding(binding)
        if (
            row is None
            or row.state != "ready"
            or source.occurred_at < row.ready_at
        ):
            await self._session.rollback()
            return BetaSpaceReservation(False)
        event_id = uuid.uuid4()
        checkpoint_sequence = await self._next_checkpoint_sequence(row)
        result = await self._session.execute(
            insert(BetaSpaceEventModel)
            .values(
                id=event_id,
                workspace_id=row.workspace_id,
                binding_id=row.id,
                receipt_id=uuid.UUID(source.receipt_id),
                update_id=source.update_id,
                telegram_chat_id=source.telegram_chat_id,
                telegram_user_id=source.telegram_user_id,
                telegram_message_id=source.telegram_message_id,
                event_kind="consent",
                media_kind="text",
                payload_sha256=source.payload_sha256,
                checkpoint_sequence=checkpoint_sequence,
                occurred_at=source.occurred_at,
                received_at=source.received_at,
            )
            .on_conflict_do_nothing()
            .returning(BetaSpaceEventModel.id)
        )
        if result.scalar_one_or_none() is None:
            await self._session.rollback()
            return BetaSpaceReservation(False)
        delivery_id = uuid.uuid4()
        request_sha = hashlib.sha256(
            f"{row.id}:{event_id}:{CONSENT_RESPONSE}".encode()
        ).hexdigest()
        self._session.add(
            BetaConsentDeliveryModel(
                id=delivery_id,
                workspace_id=row.workspace_id,
                binding_id=row.id,
                event_id=event_id,
                request_sha256=request_sha,
                state="pending",
            )
        )
        self._add_work_item(
            dedupe_key=f"consent-delivery:{delivery_id}",
            receipt_id=uuid.UUID(source.receipt_id),
            event_id=event_id,
            workspace_id=row.workspace_id,
            binding_id=row.id,
            kind="consent_delivery",
            now=source.received_at,
        )
        self._add_work_item(
            dedupe_key=f"callback-delivery:{event_id}",
            receipt_id=uuid.UUID(source.receipt_id),
            event_id=event_id,
            workspace_id=row.workspace_id,
            binding_id=row.id,
            kind="callback_delivery",
            now=source.received_at,
        )
        row.state = "consented"
        row.consented_at = source.received_at
        row.updated_at = source.received_at
        await self._enqueue_checkpoint_dispatch(
            workspace_id=row.workspace_id,
            binding_id=row.id,
            space_id=row.space_ref,
            accepted_sequence=checkpoint_sequence,
            now=source.received_at,
        )
        await self._session.commit()
        return BetaSpaceReservation(True, str(event_id), str(delivery_id))

    async def append_fact(
        self, *, binding: BetaSpaceBinding, source: BetaSpaceSource
    ) -> BetaSpaceReservation:
        row = await self._locked_binding(binding)
        if row is None or row.state != "consented":
            await self._session.rollback()
            return BetaSpaceReservation(False)
        event_id = uuid.uuid4()
        checkpoint_sequence = await self._next_checkpoint_sequence(row)
        result = await self._session.execute(
            insert(BetaSpaceEventModel)
            .values(
                id=event_id,
                workspace_id=row.workspace_id,
                binding_id=row.id,
                receipt_id=uuid.UUID(source.receipt_id),
                update_id=source.update_id,
                telegram_chat_id=source.telegram_chat_id,
                telegram_user_id=source.telegram_user_id,
                telegram_message_id=source.telegram_message_id,
                event_kind="fact",
                media_kind=source.media_kind,
                payload_sha256=source.payload_sha256,
                checkpoint_sequence=checkpoint_sequence,
                occurred_at=source.occurred_at,
                received_at=source.received_at,
            )
            .on_conflict_do_nothing()
            .returning(BetaSpaceEventModel.id)
        )
        created = result.scalar_one_or_none()
        if created is None:
            await self._session.rollback()
            return BetaSpaceReservation(False)
        if source.media_kind == "text":
            payload = await self._session.scalar(
                select(TelegramReceipt.payload).where(
                    TelegramReceipt.id == uuid.UUID(source.receipt_id)
                )
            )
            projection = _text_search_projection(
                payload,
                expected_chat_id=source.telegram_chat_id,
            )
            if projection is None:
                await self._session.rollback()
                raise BetaSpaceConfigurationError(
                    "beta_space_text_projection_invalid"
                )
            sender_label, content_text = projection
            self._session.add(
                BetaSpaceSearchDocumentModel(
                    event_id=event_id,
                    workspace_id=row.workspace_id,
                    binding_id=row.id,
                    source_kind="text",
                    sender_label=sender_label,
                    content_text=content_text,
                    occurred_at=source.occurred_at,
                    created_at=source.received_at,
                    updated_at=source.received_at,
                )
            )
        self._add_work_item(
            dedupe_key=f"{source.media_kind}:{event_id}",
            receipt_id=uuid.UUID(source.receipt_id),
            event_id=event_id,
            workspace_id=row.workspace_id,
            binding_id=row.id,
            kind="reply" if source.media_kind == "text" else "material",
            now=source.received_at,
        )
        await self._enqueue_checkpoint_dispatch(
            workspace_id=row.workspace_id,
            binding_id=row.id,
            space_id=row.space_ref,
            accepted_sequence=checkpoint_sequence,
            now=source.received_at,
        )
        await self._session.commit()
        return BetaSpaceReservation(True, str(event_id))

    async def enqueue_candidate_consent(
        self, *, receipt_id: str, now: datetime
    ) -> bool:
        """Persist one retryable owner-verification continuation for an unbound callback."""

        parsed = uuid.UUID(receipt_id)
        inserted = await self._session.scalar(
            insert(BetaSpaceWorkItemModel)
            .values(
                id=uuid.uuid4(),
                dedupe_key=f"candidate-consent:{parsed}",
                receipt_id=parsed,
                kind="candidate_consent",
                state="pending",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[BetaSpaceWorkItemModel.dedupe_key]
            )
            .returning(BetaSpaceWorkItemModel.id)
        )
        await self._session.commit()
        return inserted is not None

    async def enqueue_callback_delivery(
        self, *, receipt_id: str, now: datetime
    ) -> bool:
        """Persist one callback acknowledgement when consent is declined."""

        parsed = uuid.UUID(receipt_id)
        inserted = await self._session.scalar(
            insert(BetaSpaceWorkItemModel)
            .values(
                id=uuid.uuid4(),
                dedupe_key=f"callback-delivery:{parsed}",
                receipt_id=parsed,
                kind="callback_delivery",
                state="pending",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[BetaSpaceWorkItemModel.dedupe_key]
            )
            .returning(BetaSpaceWorkItemModel.id)
        )
        await self._session.commit()
        return inserted is not None

    def _add_work_item(
        self,
        *,
        dedupe_key: str,
        receipt_id: uuid.UUID,
        event_id: uuid.UUID,
        workspace_id: uuid.UUID,
        binding_id: uuid.UUID,
        kind: str,
        now: datetime,
    ) -> None:
        self._session.add(
            BetaSpaceWorkItemModel(
                id=uuid.uuid4(),
                dedupe_key=dedupe_key,
                receipt_id=receipt_id,
                event_id=event_id,
                workspace_id=workspace_id,
                binding_id=binding_id,
                kind=kind,
                state="pending",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    async def _enqueue_checkpoint_dispatch(
        self,
        *,
        workspace_id: uuid.UUID,
        binding_id: uuid.UUID,
        space_id: str,
        accepted_sequence: int,
        now: datetime,
    ) -> None:
        """Coalesce wake-ups in the same transaction as canonical acceptance."""

        await enqueue_beta_checkpoint_dispatch(
            self._session,
            workspace_id=workspace_id,
            binding_id=binding_id,
            space_id=space_id,
            accepted_sequence=accepted_sequence,
            now=now,
        )


    async def claim_consent_delivery(
        self, *, delivery_id: str, now: datetime
    ) -> BetaConsentDeliveryJob | None:
        candidate = await self._session.execute(
            select(BetaConsentDeliveryModel, BetaSpaceBindingModel.telegram_chat_id)
            .join(
                BetaSpaceBindingModel,
                (BetaSpaceBindingModel.id == BetaConsentDeliveryModel.binding_id)
                & (
                    BetaSpaceBindingModel.workspace_id
                    == BetaConsentDeliveryModel.workspace_id
                ),
            )
            .where(
                BetaConsentDeliveryModel.id == uuid.UUID(delivery_id),
                BetaConsentDeliveryModel.state == "pending",
                BetaSpaceBindingModel.state == "consented",
            )
            .with_for_update(skip_locked=True)
        )
        result = candidate.one_or_none()
        if result is None:
            await self._session.rollback()
            return None
        delivery, chat_id = result
        delivery.state = "reserved"
        delivery.reserved_at = now
        delivery.updated_at = now
        await self._session.commit()
        return BetaConsentDeliveryJob(delivery_id, chat_id)

    async def settle_consent_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        settled_at: datetime,
    ) -> None:
        if outcome not in {"sent", "unknown"}:
            raise ValueError("beta_consent_delivery_outcome_invalid")
        changed = await self._session.execute(
            update(BetaConsentDeliveryModel)
            .where(
                BetaConsentDeliveryModel.id == uuid.UUID(delivery_id),
                BetaConsentDeliveryModel.state == "reserved",
            )
            .values(
                state=outcome,
                provider_message_id=telegram_message_id if outcome == "sent" else None,
                settled_at=settled_at,
                updated_at=settled_at,
            )
        )
        if changed.rowcount != 1:  # type: ignore[attr-defined]
            await self._session.rollback()
            raise RuntimeError("beta_consent_delivery_state_conflict")
        await self._session.commit()

    async def _next_checkpoint_sequence(
        self, binding: BetaSpaceBindingModel
    ) -> int:
        current = await self._session.scalar(
            select(func.coalesce(func.max(BetaSpaceEventModel.checkpoint_sequence), 0))
            .where(BetaSpaceEventModel.binding_id == binding.id)
        )
        return int(current or 0) + 1

    async def _locked_binding(
        self, binding: BetaSpaceBinding
    ) -> BetaSpaceBindingModel | None:
        return cast(
            BetaSpaceBindingModel | None,
            await self._session.scalar(
                select(BetaSpaceBindingModel)
                .where(
                    BetaSpaceBindingModel.id == uuid.UUID(binding.binding_id),
                    BetaSpaceBindingModel.workspace_id == uuid.UUID(binding.workspace_id),
                    BetaSpaceBindingModel.connector_id == uuid.UUID(binding.connector_id),
                    BetaSpaceBindingModel.tenant_ref == binding.tenant_ref,
                    BetaSpaceBindingModel.space_ref == binding.space_ref,
                    BetaSpaceBindingModel.telegram_chat_id == binding.telegram_chat_id,
                    BetaSpaceBindingModel.subject_telegram_user_id
                    == binding.subject_telegram_user_id,
                    BetaSpaceBindingModel.ready_at == binding.ready_at,
                )
                .with_for_update()
            ),
        )


def _text_search_projection(
    payload: object,
    *,
    expected_chat_id: str,
) -> tuple[str, str] | None:
    """Extract one Telegram text document without trusting payload scope."""

    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    actor = message.get("from")
    content = message.get("text")
    if (
        not isinstance(chat, dict)
        or str(chat.get("id")) != expected_chat_id
        or not isinstance(actor, dict)
        or actor.get("is_bot") is not False
        or not isinstance(content, str)
        or not content.strip()
    ):
        return None
    names = [
        value.strip()
        for value in (actor.get("first_name"), actor.get("last_name"))
        if isinstance(value, str) and value.strip()
    ]
    sender_label = "Участник" if not names else " ".join(names)[:128]
    return sender_label, content


def _binding(row: BetaSpaceBindingModel) -> BetaSpaceBinding:
    return BetaSpaceBinding(
        binding_id=str(row.id),
        workspace_id=str(row.workspace_id),
        connector_id=str(row.connector_id),
        tenant_ref=row.tenant_ref,
        space_ref=row.space_ref,
        installation_ref=row.installation_ref,
        telegram_chat_id=row.telegram_chat_id,
        subject_telegram_user_id=row.subject_telegram_user_id,
        consent_sha256=row.consent_sha256,
        ready_at=row.ready_at,
        state=row.state,
        actor_policy=row.actor_policy,
    )


def _telegram_id(value: str, *, positive: bool = False) -> bool:
    try:
        parsed = int(value)
    except ValueError:
        return False
    return parsed > 0 if positive else parsed != 0
