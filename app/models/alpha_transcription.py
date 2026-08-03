"""Durable metadata for the private Alfa transcription product.

User media and transcript text never enter PostgreSQL. Rows contain only bounded
metadata, private Dropbox paths, digests, usage, and state needed for safe replay.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AlphaTranscriptionJobModel(Base):
    __tablename__ = "alpha_transcription_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_alpha_transcription_job_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["canonical_job_id"],
            ["alpha_transcription_jobs.id"],
            name="fk_alpha_transcription_job_canonical",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "source_sha256",
            name="uq_alpha_transcription_job_workspace_source",
        ),
        CheckConstraint(
            "state IN ('queued','preparing','transcribing','assembling','completed',"
            "'failed','unknown','duplicate')",
            name="ck_alpha_transcription_job_state",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_alpha_transcription_job_source_sha",
        ),
        CheckConstraint(
            "source_size_bytes IS NULL OR source_size_bytes BETWEEN 1 AND 2147483648",
            name="ck_alpha_transcription_job_source_size",
        ),
        CheckConstraint(
            "duration_millis IS NULL OR duration_millis BETWEEN 1 AND 86400000",
            name="ck_alpha_transcription_job_duration",
        ),
        CheckConstraint(
            "chunk_count BETWEEN 0 AND 512",
            name="ck_alpha_transcription_job_chunk_count",
        ),
        CheckConstraint(
            "completed_chunk_count BETWEEN 0 AND chunk_count",
            name="ck_alpha_transcription_job_completed_chunks",
        ),
        CheckConstraint(
            "billed_quantity >= 0 AND actual_microusd >= 0",
            name="ck_alpha_transcription_job_usage",
        ),
        CheckConstraint(
            "canonical_job_id IS NULL OR state = 'duplicate'",
            name="ck_alpha_transcription_job_duplicate_binding",
        ),
        CheckConstraint(
            "state <> 'completed' OR (source_sha256 IS NOT NULL AND chunk_count > 0 "
            "AND completed_chunk_count = chunk_count AND verbatim_path IS NOT NULL "
            "AND clean_path IS NOT NULL AND manifest_path IS NOT NULL "
            "AND error_code IS NULL)",
            name="ck_alpha_transcription_job_completed_payload",
        ),
        Index(
            "ix_alpha_transcription_job_workspace_state_created",
            "workspace_id",
            "state",
            "created_at",
        ),
        Index(
            "ix_alpha_transcription_job_state_next_attempt",
            "state",
            "next_attempt_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    analysis_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    language: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'ru'")
    )
    model: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'gpt-4o-mini-transcribe'")
    )
    duration_millis: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completed_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    billed_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    actual_microusd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    provider_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    verbatim_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    verbatim_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clean_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    clean_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    analysis_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlphaTranscriptionPartModel(Base):
    __tablename__ = "alpha_transcription_parts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id"],
            ["alpha_transcription_jobs.id"],
            name="fk_alpha_transcription_part_job",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "job_id",
            "part_index",
            "attempt_version",
            name="uq_alpha_transcription_part_attempt",
        ),
        CheckConstraint("part_index BETWEEN 0 AND 511", name="ck_alpha_tx_part_index"),
        CheckConstraint("attempt_version > 0", name="ck_alpha_tx_part_attempt_version"),
        CheckConstraint(
            "start_millis >= 0 AND end_millis > start_millis",
            name="ck_alpha_tx_part_interval",
        ),
        CheckConstraint(
            "state IN ('queued','running','completed','failed','unknown')",
            name="ck_alpha_tx_part_state",
        ),
        CheckConstraint("attempts IN (0,1)", name="ck_alpha_tx_part_attempts"),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_alpha_tx_part_source_sha",
        ),
        CheckConstraint(
            "source_size_bytes BETWEEN 1 AND 25000000",
            name="ck_alpha_tx_part_source_size",
        ),
        CheckConstraint(
            "billed_quantity >= 0 AND actual_microusd >= 0",
            name="ck_alpha_tx_part_usage",
        ),
        CheckConstraint(
            "(state = 'queued' AND attempts = 0 AND provider_operation_ref IS NULL "
            "AND transcript_path IS NULL AND error_code IS NULL) OR "
            "(state = 'running' AND attempts = 1 AND transcript_path IS NULL "
            "AND error_code IS NULL) OR "
            "(state = 'completed' AND attempts = 1 AND transcript_path IS NOT NULL "
            "AND transcript_sha256 IS NOT NULL AND error_code IS NULL) OR "
            "(state IN ('failed','unknown') AND attempts = 1 AND transcript_path IS NULL "
            "AND error_code IS NOT NULL)",
            name="ck_alpha_tx_part_state_payload",
        ),
        Index("ix_alpha_transcription_part_job_state", "job_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    part_index: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    start_millis: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_millis: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    provider_operation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billed_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    actual_microusd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    transcript_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcript_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
