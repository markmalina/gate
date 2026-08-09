"""Workspace- and Space-scoped Beta consent, fact and delivery ledger."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.domain import Base
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


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
            "state IN ('ready','consented','revoked','quarantined')",
            name="ck_beta_space_binding_state",
        ),
        CheckConstraint(
            "actor_policy IN ('subject_only','group_members')",
            name="ck_beta_space_binding_actor_policy",
        ),
        CheckConstraint(
            "provenance_state IN ('not_applicable','verified','quarantined')",
            name="ck_beta_space_binding_provenance_state",
        ),
        CheckConstraint(
            "(provenance_state = 'not_applicable' AND "
            "provenance_evidence_kind = 'not_applicable') OR "
            "(provenance_state = 'verified' AND "
            "provenance_evidence_kind = 'authenticated_control_plane') OR "
            "(provenance_state = 'quarantined' AND "
            "provenance_evidence_kind = 'legacy_mutable_telegram')",
            name="ck_beta_space_binding_provenance_evidence",
        ),
        CheckConstraint(
            "char_length(consent_sha256) = 64",
            name="ck_beta_space_binding_consent_sha",
        ),
        CheckConstraint(
            "(state = 'consented' AND consented_at IS NOT NULL) OR "
            "(state = 'quarantined') OR "
            "(state NOT IN ('consented','quarantined') AND consented_at IS NULL)",
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
    actor_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'subject_only'")
    )
    provenance_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'not_applicable'")
    )
    provenance_evidence_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'not_applicable'")
    )
    checkpoint_recipient_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_last_exported_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
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
        UniqueConstraint(
            "id",
            "binding_id",
            "workspace_id",
            name="uq_beta_space_event_id_binding_workspace",
        ),
        UniqueConstraint("receipt_id", name="uq_beta_space_event_receipt"),
        UniqueConstraint(
            "binding_id", "checkpoint_sequence",
            name="uq_beta_space_event_checkpoint_sequence",
        ),
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
    checkpoint_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaSpaceSearchDocumentModel(Base):
    """Exact-Space lexical projection for retained text and terminal material."""

    __tablename__ = "beta_space_search_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "binding_id", "workspace_id"],
            [
                "beta_space_events.id",
                "beta_space_events.binding_id",
                "beta_space_events.workspace_id",
            ],
            name="fk_beta_space_search_event_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source_kind IN ('text','voice','document','photo')",
            name="ck_beta_space_search_source_kind",
        ),
        CheckConstraint(
            "char_length(content_text) > 0",
            name="ck_beta_space_search_content_nonempty",
        ),
        Index(
            "ix_beta_space_search_scope_occurred",
            "workspace_id",
            "binding_id",
            "occurred_at",
            "event_id",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_label: Mapped[str] = mapped_column(String(128), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaSpaceCheckpointExportModel(Base):
    """One encrypted Wedding Space checkpoint awaiting an exact Git ack."""

    __tablename__ = "beta_space_checkpoint_exports"
    __table_args__ = (
        UniqueConstraint(
            "binding_id", "sequence_start",
            name="uq_beta_space_checkpoint_export_start",
        ),
        CheckConstraint(
            "sequence_end >= sequence_start",
            name="ck_beta_space_checkpoint_export_range",
        ),
        CheckConstraint(
            "state IN ('pending', 'acknowledged')",
            name="ck_beta_space_checkpoint_export_state",
        ),
        Index(
            "ix_beta_space_checkpoint_export_pending",
            "binding_id", "state", "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("beta_space_bindings.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_count: Mapped[int] = mapped_column(nullable=False)
    container: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    github_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

class BetaSpaceReplyModel(Base):
    """At-most-once generated reply and Telegram delivery for one Space event."""

    __tablename__ = "beta_space_replies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_space_reply_binding_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["event_id", "binding_id", "workspace_id"],
            [
                "beta_space_events.id",
                "beta_space_events.binding_id",
                "beta_space_events.workspace_id",
            ],
            name="fk_beta_space_reply_event_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("event_id", name="uq_beta_space_reply_event"),
        UniqueConstraint("request_sha256", name="uq_beta_space_reply_request"),
        CheckConstraint(
            "state IN ('generating','sending','sent','unknown')",
            name="ck_beta_space_reply_state",
        ),
        CheckConstraint(
            "char_length(request_sha256) = 64",
            name="ck_beta_space_reply_request_sha",
        ),
        CheckConstraint(
            "(state = 'generating' AND candidate_text IS NULL "
            "AND candidate_sha256 IS NULL AND provider_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(state = 'sending' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL AND provider_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(state = 'sent' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL AND provider_message_id IS NOT NULL "
            "AND settled_at IS NOT NULL) OR "
            "(state = 'unknown' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL AND provider_message_id IS NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_beta_space_reply_settlement",
        ),
        Index(
            "ix_beta_space_reply_scope_created",
            "workspace_id",
            "binding_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("beta_space_events.id", ondelete="RESTRICT"), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'generating'")
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
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
        ForeignKeyConstraint(
            ["event_id", "binding_id", "workspace_id"],
            [
                "beta_space_events.id",
                "beta_space_events.binding_id",
                "beta_space_events.workspace_id",
            ],
            name="fk_beta_consent_delivery_event_scope",
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


class BetaCheckpointDispatchIntentModel(Base):
    """One coalesced, content-free checkpoint wake-up intent per Beta Space."""

    __tablename__ = "beta_checkpoint_dispatch_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_checkpoint_dispatch_binding_workspace",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "state IN ('pending','sending','sent','failed','unknown')",
            name="ck_beta_checkpoint_dispatch_intent_state",
        ),
        CheckConstraint(
            "accepted_sequence >= 1",
            name="ck_beta_checkpoint_dispatch_intent_sequence",
        ),
        CheckConstraint(
            "dispatched_sequence >= 0 AND dispatched_sequence <= accepted_sequence",
            name="ck_beta_checkpoint_dispatch_intent_dispatched",
        ),
        CheckConstraint(
            "wake_generation >= 1 AND dispatched_wake_generation >= 0 AND "
            "dispatched_wake_generation <= wake_generation",
            name="ck_beta_checkpoint_dispatch_intent_wake_generation",
        ),
        Index(
            "ix_beta_checkpoint_dispatch_intent_due",
            "state",
            "next_attempt_at",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    space_id: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dispatched_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    wake_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    dispatched_wake_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaSpaceWorkItemModel(Base):
    """One durable, leased continuation created with an accepted Space event."""

    __tablename__ = "beta_space_work_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "binding_id", "workspace_id"],
            [
                "beta_space_events.id",
                "beta_space_events.binding_id",
                "beta_space_events.workspace_id",
            ],
            name="fk_beta_space_work_item_event_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("dedupe_key", name="uq_beta_space_work_item_dedupe"),
        CheckConstraint(
            "kind IN ('candidate_consent','callback_delivery',"
            "'consent_delivery','reply','material')",
            name="ck_beta_space_work_item_kind",
        ),
        CheckConstraint(
            "state IN ('pending','leased','retry','completed','failed','unknown')",
            name="ck_beta_space_work_item_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_beta_space_work_item_attempts",
        ),
        CheckConstraint(
            "(state = 'leased' AND lease_expires_at IS NOT NULL) OR "
            "(state != 'leased' AND lease_expires_at IS NULL)",
            name="ck_beta_space_work_item_lease",
        ),
        CheckConstraint(
            "(event_id IS NULL AND binding_id IS NULL AND workspace_id IS NULL) OR "
            "(event_id IS NOT NULL AND binding_id IS NOT NULL "
            "AND workspace_id IS NOT NULL)",
            name="ck_beta_space_work_item_scope",
        ),
        Index(
            "ix_beta_space_work_item_due",
            "state",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("beta_space_events.id", ondelete="RESTRICT"), nullable=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    binding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("beta_space_bindings.id", ondelete="RESTRICT"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
