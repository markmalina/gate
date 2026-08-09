"""Private durable voice jobs and phase-keyed Telegram deliveries for Wedding."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.domain import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class BetaWeddingVoiceJobModel(Base):
    __tablename__ = "beta_wedding_voice_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "binding_id", "workspace_id"],
            [
                "beta_space_events.id",
                "beta_space_events.binding_id",
                "beta_space_events.workspace_id",
            ],
            name="fk_beta_wedding_voice_job_event_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("event_id", name="uq_beta_wedding_voice_job_event"),
        CheckConstraint(
            "state IN ('receipt_pending','receipt_sent','transcribing','structured','failed')",
            name="ck_beta_wedding_voice_job_state",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_beta_wedding_voice_job_duration",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("beta_space_events.id", ondelete="RESTRICT"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_label: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    duration_seconds: Mapped[int | None] = mapped_column(nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structured_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'receipt_pending'")
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transcribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaWeddingVoiceDeliveryModel(Base):
    __tablename__ = "beta_wedding_voice_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "phase", name="uq_beta_wedding_voice_delivery_phase"
        ),
        CheckConstraint(
            "phase IN ('receipt','structured_result')",
            name="ck_beta_wedding_voice_delivery_phase",
        ),
        CheckConstraint(
            "state IN ('pending','sending','sent','unknown')",
            name="ck_beta_wedding_voice_delivery_state",
        ),
        CheckConstraint(
            "char_length(candidate_sha256) = 64",
            name="ck_beta_wedding_voice_delivery_sha",
        ),
        Index("ix_beta_wedding_voice_delivery_state", "state", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("beta_wedding_voice_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_text: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reserved_at: Mapped[datetime | None] = mapped_column(
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
