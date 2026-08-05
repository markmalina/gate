"""PostgreSQL isolation and idempotency adapter for Beta Space intake."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.beta_space_intake import (
    CONSENT_RESPONSE,
    BetaConsentDeliveryJob,
    BetaSpaceBinding,
    BetaSpaceReservation,
    BetaSpaceSource,
)
from app.core.config import Settings
from app.models.beta_space import (
    BetaConsentDeliveryModel,
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
)
from app.models.canonical import TransportConnector, Workspace

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
            settings.gate_beta_katya_space_enabled,
            settings.gate_beta_katya_space_ready,
            settings.gate_beta_katya_delivery_enabled,
        )
    )


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
                state="ready",
                ready_at=observed_at,
            )
        )
    return BetaSpaceBindingReconciliation("created", BETA_SPACE_REF)


class SqlBetaSpaceStore:
    """Exact-scope store; no operation supports an unscoped scan or fallback."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_binding(self, *, telegram_chat_id: str) -> BetaSpaceBinding | None:
        row = await self._session.scalar(
            select(BetaSpaceBindingModel).where(
                BetaSpaceBindingModel.telegram_chat_id == telegram_chat_id,
                BetaSpaceBindingModel.state.in_(("ready", "consented")),
            )
        )
        return _binding(row) if row is not None else None

    async def reserve_consent(
        self, *, binding: BetaSpaceBinding, source: BetaSpaceSource
    ) -> BetaSpaceReservation:
        row = await self._locked_binding(binding)
        if row is None or row.state == "consented":
            await self._session.rollback()
            return BetaSpaceReservation(False)
        event_id = uuid.uuid4()
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
        row.state = "consented"
        row.consented_at = source.received_at
        row.updated_at = source.received_at
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
        await self._session.commit()
        return BetaSpaceReservation(True, str(event_id))

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

    async def _locked_binding(
        self, binding: BetaSpaceBinding
    ) -> BetaSpaceBindingModel | None:
        return await self._session.scalar(
            select(BetaSpaceBindingModel)
            .where(
                BetaSpaceBindingModel.id == uuid.UUID(binding.binding_id),
                BetaSpaceBindingModel.workspace_id == uuid.UUID(binding.workspace_id),
                BetaSpaceBindingModel.tenant_ref == binding.tenant_ref,
                BetaSpaceBindingModel.space_ref == binding.space_ref,
                BetaSpaceBindingModel.telegram_chat_id == binding.telegram_chat_id,
                BetaSpaceBindingModel.subject_telegram_user_id
                == binding.subject_telegram_user_id,
            )
            .with_for_update()
        )


def _binding(row: BetaSpaceBindingModel) -> BetaSpaceBinding:
    return BetaSpaceBinding(
        binding_id=str(row.id),
        workspace_id=str(row.workspace_id),
        tenant_ref=row.tenant_ref,
        space_ref=row.space_ref,
        installation_ref=row.installation_ref,
        telegram_chat_id=row.telegram_chat_id,
        subject_telegram_user_id=row.subject_telegram_user_id,
        consent_sha256=row.consent_sha256,
        state=row.state,
    )


def _telegram_id(value: str, *, positive: bool = False) -> bool:
    try:
        parsed = int(value)
    except ValueError:
        return False
    return parsed > 0 if positive else parsed != 0
