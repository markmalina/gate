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
