"""Durable provider-neutral material jobs for Beta Spaces."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BetaMaterialJobModel(Base):
    """One extracted and structured voice/document/photo for one Space event."""

    __tablename__ = "beta_material_jobs"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_beta_material_job_event"),
        CheckConstraint(
            "media_kind IN ('voice','document','photo')",
            name="ck_beta_material_job_media_kind",
        ),
        CheckConstraint(
            "state IN "
            "('receipt_pending','receipt_sent','processing','structured','failed')",
            name="ck_beta_material_job_state",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_beta_material_job_duration",
        ),
        CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_beta_material_job_file_size",
        ),
        Index(
            "ix_beta_material_job_scope_created",
            "workspace_id",
            "binding_id",
            "created_at",
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
    media_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_label: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structured_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'receipt_pending'")
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaMaterialDeliveryModel(Base):
    """At-most-once receipt/result delivery for one material job phase."""

    __tablename__ = "beta_material_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "phase", name="uq_beta_material_delivery_phase"
        ),
        CheckConstraint(
            "phase IN ('receipt','structured_result')",
            name="ck_beta_material_delivery_phase",
        ),
        CheckConstraint(
            "state IN ('pending','sending','sent','unknown')",
            name="ck_beta_material_delivery_state",
        ),
        CheckConstraint(
            "char_length(candidate_sha256) = 64",
            name="ck_beta_material_delivery_sha",
        ),
        Index("ix_beta_material_delivery_state", "state", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("beta_material_jobs.id", ondelete="RESTRICT"), nullable=False
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
