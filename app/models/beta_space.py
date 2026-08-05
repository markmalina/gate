"""Workspace- and Space-scoped Beta consent, fact and delivery ledger."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BetaSpaceBindingModel(Base):
    """Immutable Telegram chat and subject binding for one isolated Beta Space."""

    __tablename__ = "beta_space_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connector_id", "workspace_id"],
            ["transport_connectors.id", "transport_connectors.workspace_id"],
            name="fk_beta_space_binding_connector_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_beta_space_binding_id_workspace"),
        UniqueConstraint(
            "workspace_id", "tenant_ref", "space_ref", name="uq_beta_space_binding_space"
        ),
        UniqueConstraint(
            "connector_id", "telegram_chat_id", name="uq_beta_space_binding_chat"
        ),
        UniqueConstraint("installation_ref", name="uq_beta_space_binding_installation"),
        UniqueConstraint("storage_ref", name="uq_beta_space_binding_storage"),
        CheckConstraint(
            "state IN ('ready','consented','revoked')",
            name="ck_beta_space_binding_state",
        ),
        CheckConstraint(
            "char_length(consent_sha256) = 64",
            name="ck_beta_space_binding_consent_sha",
        ),
        CheckConstraint(
            "(state = 'consented' AND consented_at IS NOT NULL) OR "
            "(state != 'consented' AND consented_at IS NULL)",
            name="ck_beta_space_binding_consented_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    space_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    telegram_chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_telegram_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'ready'")
    )
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaSpaceEventModel(Base):
    """Append-only consent or post-consent fact provenance for one exact Space."""

    __tablename__ = "beta_space_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_space_event_binding_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("receipt_id", name="uq_beta_space_event_receipt"),
        UniqueConstraint("binding_id", "update_id", name="uq_beta_space_event_update"),
        UniqueConstraint(
            "binding_id", "telegram_message_id", name="uq_beta_space_event_message"
        ),
        CheckConstraint(
            "event_kind IN ('consent','fact')", name="ck_beta_space_event_kind"
        ),
        CheckConstraint(
            "media_kind IN ('text','voice','photo','document')",
            name="ck_beta_space_event_media_kind",
        ),
        CheckConstraint(
            "char_length(payload_sha256) = 64", name="ck_beta_space_event_payload_sha"
        ),
        Index(
            "ix_beta_space_event_scope_occurred",
            "workspace_id",
            "binding_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"), nullable=False
    )
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    media_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaConsentDeliveryModel(Base):
    """At-most-once deterministic consent acknowledgement reservation."""

    __tablename__ = "beta_consent_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_consent_delivery_binding_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("event_id", name="uq_beta_consent_delivery_event"),
        UniqueConstraint("request_sha256", name="uq_beta_consent_delivery_request"),
        CheckConstraint(
            "state IN ('pending','reserved','sent','failed','unknown')",
            name="ck_beta_consent_delivery_state",
        ),
        CheckConstraint(
            "char_length(request_sha256) = 64",
            name="ck_beta_consent_delivery_request_sha",
        ),
        CheckConstraint(
            "(state IN ('pending','reserved') AND provider_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(state = 'sent' AND provider_message_id IS NOT NULL "
            "AND settled_at IS NOT NULL) OR "
            "(state IN ('failed','unknown') AND provider_message_id IS NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_beta_consent_delivery_settlement",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("beta_space_events.id", ondelete="RESTRICT"), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
