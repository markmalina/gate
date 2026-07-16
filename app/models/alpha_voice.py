"""Durable, scoped metadata and queue state for future Alpha voice handling."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
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


class CanonicalVoiceMediaModel(Base):
    """Sanitized Telegram voice metadata; it never contains audio or a file path."""

    __tablename__ = "canonical_voice_media"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_canonical_voice_media_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["canonical_message_id", "dialog_id", "workspace_id"],
            [
                "canonical_messages.id",
                "canonical_messages.dialog_id",
                "canonical_messages.workspace_id",
            ],
            name="fk_canonical_voice_media_message_dialog_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "canonical_message_id",
            name="uq_canonical_voice_media_message",
        ),
        UniqueConstraint(
            "id",
            "canonical_message_id",
            "dialog_id",
            "workspace_id",
            name="uq_canonical_voice_media_id_message_scope",
        ),
        CheckConstraint(
            "char_length(provider_file_id) BETWEEN 1 AND 512",
            name="ck_canonical_voice_media_file_id",
        ),
        CheckConstraint(
            "file_unique_id_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_canonical_voice_media_unique_hash",
        ),
        CheckConstraint(
            "duration_seconds BETWEEN 1 AND 600",
            name="ck_canonical_voice_media_duration",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes BETWEEN 1 AND 19000000",
            name="ck_canonical_voice_media_size",
        ),
        CheckConstraint(
            "mime_type IS NULL OR mime_type IN ('audio/ogg', 'audio/opus')",
            name="ck_canonical_voice_media_mime",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_unique_id_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlphaVoiceJobModel(Base):
    """One idempotent transcription operation, initially queue-only and dormant."""

    __tablename__ = "alpha_voice_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_alpha_voice_jobs_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_alpha_voice_jobs_dialog_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["installation_id", "dialog_id", "workspace_id"],
            [
                "agent_installations.id",
                "agent_installations.dialog_id",
                "agent_installations.workspace_id",
            ],
            name="fk_alpha_voice_jobs_installation_dialog_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["canonical_message_id", "dialog_id", "workspace_id"],
            [
                "canonical_messages.id",
                "canonical_messages.dialog_id",
                "canonical_messages.workspace_id",
            ],
            name="fk_alpha_voice_jobs_message_dialog_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["media_id", "canonical_message_id", "dialog_id", "workspace_id"],
            [
                "canonical_voice_media.id",
                "canonical_voice_media.canonical_message_id",
                "canonical_voice_media.dialog_id",
                "canonical_voice_media.workspace_id",
            ],
            name="fk_alpha_voice_jobs_media_message_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "canonical_message_id",
            "operation",
            name="uq_alpha_voice_jobs_message_operation",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_alpha_voice_jobs_id_workspace"),
        CheckConstraint(
            "operation = 'transcription'",
            name="ck_alpha_voice_jobs_operation",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed', 'unknown')",
            name="ck_alpha_voice_jobs_state",
        ),
        CheckConstraint(
            "attempts IN (0, 1)",
            name="ck_alpha_voice_jobs_attempts",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_alpha_voice_jobs_version",
        ),
        CheckConstraint(
            "intake_code = 'alpha_voice_queued'",
            name="ck_alpha_voice_jobs_intake_code",
        ),
        CheckConstraint(
            "transcript_text IS NULL OR char_length(transcript_text) <= 100000",
            name="ck_alpha_voice_jobs_transcript_size",
        ),
        CheckConstraint(
            "(state = 'queued' AND attempts = 0 "
            "AND transcript_text IS NULL AND error_code IS NULL "
            "AND provider_operation_ref IS NULL) OR "
            "(state = 'running' AND attempts = 1 "
            "AND transcript_text IS NULL AND error_code IS NULL "
            "AND provider_operation_ref IS NOT NULL) OR "
            "(state = 'completed' AND attempts = 1 "
            "AND transcript_text IS NOT NULL AND error_code IS NULL "
            "AND provider_operation_ref IS NOT NULL) OR "
            "(state IN ('failed', 'unknown') AND attempts = 1 "
            "AND transcript_text IS NULL AND error_code IS NOT NULL "
            "AND provider_operation_ref IS NOT NULL)",
            name="ck_alpha_voice_jobs_state_payload",
        ),
        Index(
            "ix_alpha_voice_jobs_workspace_state_created",
            "workspace_id",
            "state",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'transcription'")
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    intake_code: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'alpha_voice_queued'")
    )
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_operation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
