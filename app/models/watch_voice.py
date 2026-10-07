"""Owner-only watch voice originals, cursor and GitHub delivery receipts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class WatchVoiceCursor(Base):
    __tablename__ = "watch_voice_cursors"

    workspace_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text)
    future_cutover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchVoiceNote(Base):
    __tablename__ = "watch_voice_notes"
    __table_args__ = (
        UniqueConstraint(
            "workspace_key",
            "account_id",
            "file_id",
            "revision",
            name="uq_watch_voice_source_revision",
        ),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 26214400", name="ck_watch_voice_size"),
        CheckConstraint(
            "transcription_state IN ('received','processing','transcribed','rejected','unknown')",
            name="ck_watch_voice_transcription",
        ),
        CheckConstraint(
            "delivery_state IN ('pending','sending','unknown','delivered','blocked')",
            name="ck_watch_voice_delivery",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_key: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[str] = mapped_column(String(255))
    revision: Mapped[str] = mapped_column(String(255))
    source_path: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dropbox_modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transcription_state: Mapped[str] = mapped_column(String(16), default="received")
    transcript: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    transcribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(16), default="pending")
    next_part: Mapped[int] = mapped_column(Integer, default=0)
    issue_number: Mapped[int | None] = mapped_column(Integer)
    issue_url: Mapped[str | None] = mapped_column(String(512))
    github_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(128))


class WatchVoiceDrainRun(Base):
    """Permanent replay guard; hashes only, never credentials or source payload."""

    __tablename__ = "watch_voice_drain_runs"
    run_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    jwt_key: Mapped[str] = mapped_column(String(64), unique=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WatchVoiceRecoveryClaim(Base):
    """One permanent recovery per original note, preserving the rejected attempt."""

    __tablename__ = "watch_voice_recovery_claims"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('claimed','transcribed','rejected','unknown')",
            name="ck_watch_voice_recovery_outcome",
        ),
    )

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("watch_voice_notes.id"), primary_key=True
    )
    original_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_budget_reservations.id")
    )
    original_error: Mapped[str] = mapped_column(String(128))
    original_model: Mapped[str] = mapped_column(String(64))
    original_attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorization_sha: Mapped[str] = mapped_column(String(40))
    authorization_kind: Mapped[str] = mapped_column(String(32))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(16))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(128))


class WatchVoicePostPaymentClaim(Base):
    """One fixed paid continuation; the permanent first recovery stays intact."""

    __tablename__ = "watch_voice_post_payment_claims"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('claimed','transcribed','rejected','unknown')",
            name="ck_watch_voice_post_payment_outcome",
        ),
    )

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("watch_voice_recovery_claims.note_id"), primary_key=True
    )
    previous_reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_budget_reservations.id")
    )
    previous_error: Mapped[str] = mapped_column(String(128))
    previous_model: Mapped[str] = mapped_column(String(64))
    previous_attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorization_sha: Mapped[str] = mapped_column(String(40))
    authorization_kind: Mapped[str] = mapped_column(String(32))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(16))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(128))
