"""Persistence model for immutable BECOME I4 recovery preparation receipts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BecomeI4RecoveryReceiptModel(Base):
    """One default-off recovery version linked to an immutable failed slot."""

    __tablename__ = "become_i4_recovery_receipts"
    __table_args__ = (
        UniqueConstraint(
            "failed_slot_id",
            name="uq_become_i4_recovery_failed_slot",
        ),
        UniqueConstraint(
            "correlation_id",
            "stage",
            "attempt_version",
            name="uq_become_i4_recovery_attempt",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-i4-recovery-receipt@1'",
            name="ck_become_i4_recovery_schema",
        ),
        CheckConstraint(
            "stage = 'mayaki' AND attempt_version = 2",
            name="ck_become_i4_recovery_stage_version",
        ),
        CheckConstraint(
            "state = 'prepared' AND enabled = false",
            name="ck_become_i4_recovery_default_off",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64 "
            "AND char_length(original_request_digest) = 64 "
            "AND char_length(original_receipt_digest) = 64 "
            "AND char_length(original_source_sha) = 40 "
            "AND char_length(target_source_sha) = 40",
            name="ck_become_i4_recovery_digests",
        ),
        Index(
            "ix_become_i4_recovery_workspace_state",
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
    failed_slot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_voice_chain_slots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    original_request_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    original_receipt_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    original_source_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    target_source_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BecomeI4RecoveryAttemptModel(Base):
    """Immutable identity for one append-only recovery attempt version 2."""

    __tablename__ = "become_i4_recovery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "recovery_receipt_id",
            name="uq_become_i4_recovery_attempt_receipt",
        ),
        UniqueConstraint(
            "correlation_id",
            "attempt_version",
            name="uq_become_i4_recovery_attempt_version",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-i4-recovery-attempt@2' "
            "AND attempt_version = 2 AND state = 'prepared'",
            name="ck_become_i4_recovery_attempt_identity",
        ),
        CheckConstraint(
            "max_posts = 3",
            name="ck_become_i4_recovery_attempt_max_posts",
        ),
        CheckConstraint(
            "char_length(prepared_recovery_digest) = 64 "
            "AND char_length(original_request_digest) = 64 "
            "AND char_length(original_receipt_digest) = 64 "
            "AND char_length(relations_receipt_digest) = 64 "
            "AND char_length(transcript_digest) = 64 "
            "AND char_length(contract_digest) = 64 "
            "AND char_length(mayaki_source_sha) = 40 "
            "AND char_length(oko_source_sha) = 40 "
            "AND char_length(take_source_sha) = 40 "
            "AND char_length(attempt_digest) = 64",
            name="ck_become_i4_recovery_attempt_digests",
        ),
        Index(
            "ix_become_i4_recovery_attempt_workspace",
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
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_voice_correlations.id", ondelete="CASCADE"),
        nullable=False,
    )
    recovery_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_i4_recovery_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    max_posts: Mapped[int] = mapped_column(Integer, nullable=False)
    prepared_recovery_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    original_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    original_receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    relations_receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    transcript_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    mayaki_source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    oko_source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    take_source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BecomeI4RecoveryStageEventModel(Base):
    """Append-only reservation or terminal receipt for a recovery stage."""

    __tablename__ = "become_i4_recovery_stage_events"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "stage",
            "event_sequence",
            name="uq_become_i4_recovery_stage_sequence",
        ),
        CheckConstraint(
            "stage IN ('mayaki', 'oko', 'take') "
            "AND ordinal IN (2, 3, 4)",
            name="ck_become_i4_recovery_stage_identity",
        ),
        CheckConstraint(
            "(event_sequence = 1 AND event_type = 'reserved') "
            "OR (event_sequence = 2 "
            "AND event_type IN ('completed', 'failed', 'unknown'))",
            name="ck_become_i4_recovery_event_order",
        ),
        CheckConstraint(
            "char_length(request_digest) = 64 "
            "AND char_length(source_sha) = 40 "
            "AND char_length(event_digest) = 64 "
            "AND (receipt_digest IS NULL "
            "OR char_length(receipt_digest) = 64)",
            name="ck_become_i4_recovery_event_digests",
        ),
        Index(
            "ix_become_i4_recovery_event_attempt",
            "attempt_id",
            "ordinal",
            "event_sequence",
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
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_i4_recovery_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    receipt_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
