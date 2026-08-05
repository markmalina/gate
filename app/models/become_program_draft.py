"""Durable owner-only Slice 2 PROGRAM draft state."""

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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BecomePilotProgramDraftModel(Base):
    __tablename__ = "become_pilot_program_drafts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('reserved','calling','pending_display','presented',"
            "'awaiting_correction','approved','unknown','invalidated')",
            name="ck_become_program_state",
        ),
        CheckConstraint("latest_version >= 0", name="ck_become_program_latest_version"),
        CheckConstraint(
            "char_length(source_transcript_digest) = 64",
            name="ck_become_program_source_digest",
        ),
        CheckConstraint(
            "(state = 'approved' AND approved_version = latest_version "
            "AND char_length(approved_digest) = 64 AND approved_at IS NOT NULL) "
            "OR (state != 'approved' AND approved_version IS NULL "
            "AND approved_digest IS NULL AND approved_at IS NULL)",
            name="ck_become_program_approval",
        ),
        UniqueConstraint(
            "transcript_review_id",
            "source_transcript_version",
            "source_transcript_digest",
            name="uq_become_program_source",
        ),
        UniqueConstraint("callback_ref", name="uq_become_program_callback"),
        Index("ix_become_program_workspace_state", "workspace_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    transcript_review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_pilot_transcript_reviews.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_transcript_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_transcript_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dialogs.id", ondelete="RESTRICT"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    callback_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BecomePilotProgramVersionModel(Base):
    __tablename__ = "become_pilot_program_versions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_become_program_version_positive"),
        CheckConstraint(
            "source IN ('provider','human_correction')",
            name="ck_become_program_version_source",
        ),
        CheckConstraint(
            "char_length(program_digest) = 64",
            name="ck_become_program_version_digest",
        ),
        CheckConstraint(
            "(program_body IS NOT NULL AND purged_at IS NULL) OR "
            "(program_body IS NULL AND purged_at IS NOT NULL)",
            name="ck_become_program_version_retention",
        ),
        UniqueConstraint(
            "draft_id", "version", name="uq_become_program_version"
        ),
        Index("ix_become_program_version_expiry", "expires_at", "purged_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_pilot_program_drafts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    program_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    program_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BecomePilotProgramDeliveryModel(Base):
    __tablename__ = "become_pilot_program_deliveries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('presentation','redisplay','correction_prompt','confirmation')",
            name="ck_become_program_delivery_kind",
        ),
        CheckConstraint(
            "version > 0 AND char_length(program_digest) = 64 "
            "AND char_length(request_digest) = 64",
            name="ck_become_program_delivery_binding",
        ),
        CheckConstraint(
            "outcome IN ('pending','reserved','sent','failed','unknown')",
            name="ck_become_program_delivery_outcome",
        ),
        CheckConstraint(
            "(outcome IN ('pending','reserved') AND telegram_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(outcome = 'sent' AND telegram_message_id IS NOT NULL "
            "AND settled_at IS NOT NULL) OR "
            "(outcome IN ('failed','unknown') AND telegram_message_id IS NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_become_program_delivery_settlement",
        ),
        UniqueConstraint(
            "draft_id",
            "kind",
            "version",
            "program_digest",
            name="uq_become_program_delivery_operation",
        ),
        UniqueConstraint("request_digest", name="uq_become_program_delivery_request"),
        Index("ix_become_program_delivery_outcome", "outcome", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("become_pilot_program_drafts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    program_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    callback_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
