"""Persistence models for the isolated BECOME controlled intake."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BecomeControlledIntakeReceiptModel(Base):
    """One content-free receipt bound to a durable Telegram update."""

    __tablename__ = "become_controlled_intake_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_intake_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "receipt_key",
            name="uq_become_intake_workspace_key",
        ),
        UniqueConstraint(
            "source_receipt_id",
            "intent",
            name="uq_become_intake_source_intent",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-controlled-intake@1'",
            name="ck_become_intake_schema",
        ),
        CheckConstraint(
            "connector_key = 'telegram_become_group'",
            name="ck_become_intake_connector",
        ),
        CheckConstraint(
            "intent = 'registration_confirmed'",
            name="ck_become_intake_intent",
        ),
        CheckConstraint(
            "group_state = 'owner_only' AND member_count = 2",
            name="ck_become_intake_owner_only",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64",
            name="ck_become_intake_digest",
        ),
        Index(
            "ix_become_intake_installation_created",
            "installation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    update_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    group_state: Mapped[str] = mapped_column(String(32), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BecomeControlledConsentReceiptModel(Base):
    """One explicit content-free consent receipt after controlled registration."""

    __tablename__ = "become_controlled_consent_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_consent_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "receipt_key",
            name="uq_become_consent_workspace_key",
        ),
        UniqueConstraint(
            "source_receipt_id",
            name="uq_become_consent_source_receipt",
        ),
        UniqueConstraint(
            "installation_id",
            "subject_ref",
            "policy_version",
            "intent",
            name="uq_become_consent_subject_policy",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-controlled-consent-intake@1'",
            name="ck_become_consent_schema",
        ),
        CheckConstraint(
            "connector_key = 'telegram_become_group'",
            name="ck_become_consent_connector",
        ),
        CheckConstraint(
            "intent = 'consent_accepted'",
            name="ck_become_consent_intent",
        ),
        CheckConstraint(
            "group_state = 'owner_only' AND member_count = 2",
            name="ck_become_consent_owner_only",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64 AND char_length(policy_digest) = 64",
            name="ck_become_consent_digests",
        ),
        Index(
            "ix_become_consent_installation_created",
            "installation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    quest_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    correlation_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    action_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    group_state: Mapped[str] = mapped_column(String(32), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BecomeRelationsConsentHandoffModel(Base):
    """Provider-disabled Relations command projected from one consent receipt."""

    __tablename__ = "become_relations_consent_handoffs"
    __table_args__ = (
        UniqueConstraint(
            "consent_receipt_id",
            name="uq_become_relations_handoff_consent",
        ),
        UniqueConstraint(
            "workspace_id",
            "handoff_key",
            name="uq_become_relations_handoff_workspace_key",
        ),
        CheckConstraint(
            "schema_name = 'relations.controlled-consent-command@1'",
            name="ck_become_relations_handoff_schema",
        ),
        CheckConstraint(
            "status IN ('pending_delivery', 'pending_execution', 'executed')",
            name="ck_become_relations_handoff_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_become_relations_handoff_attempts",
        ),
        CheckConstraint(
            "("
            "status = 'pending_delivery' "
            "AND execution_digest IS NULL "
            "AND execution_payload IS NULL "
            "AND domain_event_id IS NULL "
            "AND domain_event_digest IS NULL "
            "AND executed_at IS NULL"
            ") OR ("
            "status = 'pending_execution' "
            "AND char_length(acceptance_digest) = 64 "
            "AND acceptance_payload IS NOT NULL "
            "AND delivered_at IS NOT NULL "
            "AND execution_digest IS NULL "
            "AND execution_payload IS NULL "
            "AND executed_at IS NULL"
            ") OR ("
            "status = 'executed' "
            "AND char_length(execution_digest) = 64 "
            "AND execution_payload IS NOT NULL "
            "AND domain_event_id LIKE 'consent_evt_%' "
            "AND char_length(domain_event_digest) = 64 "
            "AND executed_at IS NOT NULL"
            ")",
            name="ck_become_relations_handoff_execution",
        ),
        CheckConstraint(
            "char_length(command_digest) = 64",
            name="ck_become_relations_handoff_digest",
        ),
        Index(
            "ix_become_relations_handoff_workspace_created",
            "workspace_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_controlled_consent_receipts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    handoff_key: Mapped[str] = mapped_column(String(128), nullable=False)
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    acceptance_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    acceptance_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    execution_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    execution_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    domain_event_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    domain_event_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BecomeMediaIntakeModel(Base):
    """Consent-bound BECOME raw media with enforced 7/30-day retention."""

    __tablename__ = "become_media_intakes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_media_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "source_receipt_id",
            name="uq_become_media_source_receipt",
        ),
        UniqueConstraint(
            "workspace_id",
            "intake_key",
            name="uq_become_media_workspace_key",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-media-intake@1'",
            name="ck_become_media_schema",
        ),
        CheckConstraint(
            "kind IN ('voice', 'video')",
            name="ck_become_media_kind",
        ),
        CheckConstraint(
            "status IN ('pending_download', 'archived', 'raw_purged')",
            name="ck_become_media_status",
        ),
        CheckConstraint(
            "transcript_state IN ('none', 'confirmed', 'purged')",
            name="ck_become_media_transcript_state",
        ),
        CheckConstraint(
            "char_length(file_unique_digest) = 64",
            name="ck_become_media_file_digest",
        ),
        CheckConstraint(
            "declared_size_bytes IS NULL OR "
            "(declared_size_bytes > 0 AND declared_size_bytes <= 20000000)",
            name="ck_become_media_declared_size",
        ),
        CheckConstraint(
            "("
            "status = 'pending_download' "
            "AND raw_data IS NULL "
            "AND raw_sha256 IS NULL "
            "AND raw_size_bytes IS NULL "
            "AND raw_media_type IS NULL "
            "AND raw_purged_at IS NULL"
            ") OR ("
            "status = 'archived' "
            "AND raw_data IS NOT NULL "
            "AND char_length(raw_sha256) = 64 "
            "AND raw_size_bytes > 0 "
            "AND raw_size_bytes <= 20000000 "
            "AND raw_media_type IS NOT NULL "
            "AND raw_purged_at IS NULL"
            ") OR ("
            "status = 'raw_purged' "
            "AND raw_data IS NULL "
            "AND char_length(raw_sha256) = 64 "
            "AND raw_size_bytes > 0 "
            "AND raw_media_type IS NOT NULL "
            "AND raw_purged_at IS NOT NULL"
            ")",
            name="ck_become_media_raw_state",
        ),
        CheckConstraint(
            "("
            "transcript_state = 'none' "
            "AND confirmed_transcript IS NULL "
            "AND transcript_digest IS NULL "
            "AND transcript_confirmed_at IS NULL "
            "AND transcript_expires_at IS NULL "
            "AND transcript_purged_at IS NULL"
            ") OR ("
            "transcript_state = 'confirmed' "
            "AND confirmed_transcript IS NOT NULL "
            "AND char_length(transcript_digest) = 64 "
            "AND transcript_confirmed_at IS NOT NULL "
            "AND transcript_expires_at IS NOT NULL "
            "AND transcript_purged_at IS NULL"
            ") OR ("
            "transcript_state = 'purged' "
            "AND confirmed_transcript IS NULL "
            "AND char_length(transcript_digest) = 64 "
            "AND transcript_confirmed_at IS NOT NULL "
            "AND transcript_expires_at IS NOT NULL "
            "AND transcript_purged_at IS NOT NULL"
            ")",
            name="ck_become_media_transcript_retention",
        ),
        Index(
            "ix_become_media_raw_expiry",
            "status",
            "raw_expires_at",
        ),
        Index(
            "ix_become_media_transcript_expiry",
            "transcript_state",
            "transcript_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    registration_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_controlled_intake_receipts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    consent_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_controlled_consent_receipts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    relations_handoff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_relations_consent_handoffs.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    intake_key: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    update_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    file_unique_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_size_bytes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_media_type: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    raw_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    raw_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    transcript_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="none",
    )
    confirmed_transcript: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    transcript_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    transcript_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    transcript_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    transcript_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BecomeMediaPromptModel(Base):
    """One durable BECOME-only reply anchor after executed consent."""

    __tablename__ = "become_media_prompts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_media_prompt_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "installation_id",
            name="uq_become_media_prompt_installation",
        ),
        UniqueConstraint(
            "workspace_id",
            "prompt_key",
            name="uq_become_media_prompt_workspace_key",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-media-prompt@1'",
            name="ck_become_media_prompt_schema",
        ),
        CheckConstraint(
            "state IN ('sending', 'sent', 'failed', 'blocked', 'unknown')",
            name="ck_become_media_prompt_state",
        ),
        CheckConstraint(
            "char_length(prompt_digest) = 64",
            name="ck_become_media_prompt_digest",
        ),
        CheckConstraint(
            "("
            "state = 'sending' "
            "AND provider_message_id IS NULL "
            "AND error_code IS NULL "
            "AND sent_at IS NULL"
            ") OR ("
            "state = 'sent' "
            "AND provider_message_id IS NOT NULL "
            "AND error_code IS NULL "
            "AND sent_at IS NOT NULL"
            ") OR ("
            "state IN ('failed', 'blocked', 'unknown') "
            "AND provider_message_id IS NULL "
            "AND error_code IS NOT NULL "
            "AND sent_at IS NULL"
            ")",
            name="ck_become_media_prompt_outcome",
        ),
        Index(
            "ix_become_media_prompt_workspace_state",
            "workspace_id",
            "state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    consent_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_controlled_consent_receipts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    relations_handoff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "become_relations_consent_handoffs.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    prompt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BecomeVoiceCorrelationModel(Base):
    """Content-free root for one consent-bound BECOME voice intake."""

    __tablename__ = "become_voice_correlations"
    __table_args__ = (
        UniqueConstraint(
            "media_intake_id",
            name="uq_become_voice_correlation_media",
        ),
        UniqueConstraint(
            "workspace_id",
            "correlation_ref",
            name="uq_become_voice_correlation_workspace_ref",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-voice-correlation@1'",
            name="ck_become_voice_correlation_schema",
        ),
        CheckConstraint(
            "correlation_ref LIKE 'synthetic://correlation/voice/%' "
            "AND char_length(intake_digest) = 64",
            name="ck_become_voice_correlation_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_intake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_media_intakes.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    intake_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BecomeVoiceChainSlotModel(Base):
    """Provider-disabled receipt slot for one BECOME chain stage."""

    __tablename__ = "become_voice_chain_slots"
    __table_args__ = (
        UniqueConstraint(
            "correlation_id",
            "stage",
            name="uq_become_voice_chain_slot_stage",
        ),
        UniqueConstraint(
            "correlation_id",
            "position",
            name="uq_become_voice_chain_slot_position",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-chain-slot@1'",
            name="ck_become_voice_chain_slot_schema",
        ),
        CheckConstraint(
            "(stage = 'relations' AND position = 1) OR "
            "(stage = 'mayaki' AND position = 2) OR "
            "(stage = 'oko' AND position = 3) OR "
            "(stage = 'take' AND position = 4)",
            name="ck_become_voice_chain_slot_position",
        ),
        CheckConstraint(
            "state IN "
            "('blocked', 'ready', 'requested', 'completed', 'failed', 'unknown')",
            name="ck_become_voice_chain_slot_state",
        ),
        CheckConstraint(
            "("
            "state IN ('blocked', 'ready') "
            "AND request_digest IS NULL "
            "AND request_payload IS NULL "
            "AND receipt_digest IS NULL "
            "AND receipt_payload IS NULL "
            "AND source_sha IS NULL "
            "AND terminal_at IS NULL"
            ") OR ("
            "state = 'requested' "
            "AND char_length(request_digest) = 64 "
            "AND request_payload IS NOT NULL "
            "AND receipt_digest IS NULL "
            "AND receipt_payload IS NULL "
            "AND char_length(source_sha) = 40 "
            "AND terminal_at IS NULL"
            ") OR ("
            "state IN ('completed', 'failed', 'unknown') "
            "AND char_length(request_digest) = 64 "
            "AND request_payload IS NOT NULL "
            "AND char_length(receipt_digest) = 64 "
            "AND receipt_payload IS NOT NULL "
            "AND char_length(source_sha) = 40 "
            "AND terminal_at IS NOT NULL"
            ")",
            name="ck_become_voice_chain_slot_outcome",
        ),
        CheckConstraint(
            "(stage = 'take' AND state NOT IN ('blocked', 'ready') "
            "AND char_length(mapping_digest) = 64) OR "
            "((stage != 'take' OR state IN ('blocked', 'ready')) "
            "AND mapping_digest IS NULL)",
            name="ck_become_voice_chain_take_mapping",
        ),
        Index(
            "ix_become_voice_chain_workspace_state",
            "workspace_id",
            "stage",
            "state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_voice_correlations.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    request_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    request_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    receipt_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    receipt_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    source_sha: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    mapping_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

class BecomeVoiceTranscriptionAttemptModel(Base):
    """One durable provider-call grant for one BECOME voice correlation."""

    __tablename__ = "become_voice_transcription_attempts"
    __table_args__ = (
        UniqueConstraint(
            "correlation_id",
            name="uq_become_voice_transcription_correlation",
        ),
        UniqueConstraint(
            "media_intake_id",
            name="uq_become_voice_transcription_media",
        ),
        UniqueConstraint(
            "workspace_id",
            "grant_key",
            name="uq_become_voice_transcription_workspace_grant",
        ),
        UniqueConstraint(
            "workspace_id",
            "reservation_key",
            name="uq_become_voice_transcription_workspace_reservation",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-voice-transcription@1'",
            name="ck_become_voice_transcription_schema",
        ),
        CheckConstraint(
            "state IN ('reserving', 'reserved', 'calling', "
            "'succeeded_unsettled', 'confirmed', 'rejected', "
            "'unknown', 'blocked')",
            name="ck_become_voice_transcription_state",
        ),
        CheckConstraint(
            "grant_key = 'become-investor-demo-transcription-v1'",
            name="ck_become_voice_transcription_grant",
        ),
        CheckConstraint(
            "requested_microusd > 0 AND "
            "(billed_quantity IS NULL OR billed_quantity >= 0)",
            name="ck_become_voice_transcription_amounts",
        ),
        CheckConstraint(
            "actual_microusd IS NULL OR "
            "(actual_microusd >= 0 "
            "AND actual_microusd <= requested_microusd)",
            name="ck_become_voice_transcription_cost",
        ),
        CheckConstraint(
            "(state IN ('reserving', 'reserved', 'blocked') "
            "AND provider_call_started_at IS NULL) OR "
            "(state IN ('calling', 'succeeded_unsettled', 'confirmed', "
            "'rejected', 'unknown') "
            "AND provider_call_started_at IS NOT NULL)",
            name="ck_become_voice_transcription_call_boundary",
        ),
        CheckConstraint(
            "(state IN ('succeeded_unsettled', 'confirmed') "
            "AND billed_quantity IS NOT NULL "
            "AND actual_microusd IS NOT NULL "
            "AND char_length(receipt_digest) = 64 "
            "AND reason_code IS NULL) OR "
            "(state NOT IN ('succeeded_unsettled', 'confirmed') "
            "AND billed_quantity IS NULL "
            "AND actual_microusd IS NULL "
            "AND receipt_digest IS NULL)",
            name="ck_become_voice_transcription_success",
        ),
        Index(
            "ix_become_voice_transcription_workspace_state",
            "workspace_id",
            "state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_voice_correlations.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_intake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_media_intakes.id", ondelete="CASCADE"),
        nullable=False,
    )
    grant_key: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reservation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_microusd: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_call_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_operation_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    billed_quantity: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    actual_microusd: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    reason_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    receipt_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
