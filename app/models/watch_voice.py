"""Owner-only watch voice originals, cursor and GitHub delivery receipts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
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
        Index("ix_watch_voice_note_scope_time", "workspace_key", "account_id", "received_at", "id"),
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
    delivery_writer: Mapped[str | None] = mapped_column(String(128))
    next_part: Mapped[int] = mapped_column(Integer, default=0)
    issue_number: Mapped[int | None] = mapped_column(Integer)
    issue_url: Mapped[str | None] = mapped_column(String(512))
    github_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(128))
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_attempt_count: Mapped[int | None] = mapped_column(Integer)
    provider_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_request_count: Mapped[int | None] = mapped_column(Integer)
    provider_finished_count: Mapped[int | None] = mapped_column(Integer)
    provider_elapsed_ms: Mapped[int | None] = mapped_column(Integer)


class WatchVoiceCycle(Base):
    """Durable, payload-free cycle evidence shared by all API/worker processes."""

    __tablename__ = "watch_voice_cycles"
    __table_args__ = (
        Index("ix_watch_voice_cycle_scope_time", "workspace_key", "account_id", "started_at"),
        CheckConstraint("driver IN ('actions','persistent')", name="ck_watch_voice_cycle_driver"),
        CheckConstraint("outcome IN ('running','success','failure','cancelled')",
                        name="ck_watch_voice_cycle_outcome"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_key: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(255))
    driver: Mapped[str] = mapped_column(String(16))
    source_sha: Mapped[str | None] = mapped_column(String(40))
    owns_cursor: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    phase: Mapped[str] = mapped_column(String(32))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_error: Mapped[str | None] = mapped_column(String(128))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(16), default="running")
    error_code: Mapped[str | None] = mapped_column(String(128))


class WatchVoiceSource(Base):
    """Durable discovery/download queue, including files outside audio bounds."""

    __tablename__ = "watch_voice_sources"
    __table_args__ = (
        UniqueConstraint("workspace_key", "account_id", "file_id", "revision",
                         name="uq_watch_voice_discovered_revision"),
        Index("ix_watch_voice_source_scope_state", "workspace_key", "account_id", "state"),
        CheckConstraint("state IN ('pending','downloading','retry','archived','rejected')",
                        name="ck_watch_voice_source_state"),
        CheckConstraint("attempt_count >= 0", name="ck_watch_voice_source_attempt_count"),
        CheckConstraint(
            "card_state IN ('legacy','pending','sending','unknown','saved','blocked')",
            name="ck_watch_voice_source_card_state",
        ),
        Index("ix_watch_voice_source_card", "workspace_key", "account_id", "card_state"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_key: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[str] = mapped_column(String(255))
    revision: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    source_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    dropbox_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(128))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_id: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Historical sources stay legacy: rollout never backfills extra Inbox cards.
    card_state: Mapped[str] = mapped_column(String(16), default="legacy", server_default="legacy")
    card_body: Mapped[str | None] = mapped_column(Text)
    card_body_sha256: Mapped[str | None] = mapped_column(String(64))
    card_title: Mapped[str | None] = mapped_column(String(256))
    card_claim_id: Mapped[str | None] = mapped_column(String(36))
    card_writer: Mapped[str | None] = mapped_column(String(128))
    card_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    card_issue_number: Mapped[int | None] = mapped_column(Integer)
    card_issue_url: Mapped[str | None] = mapped_column(String(512))
    card_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    card_error: Mapped[str | None] = mapped_column(String(128))


class WatchVoiceIncident(Base):
    """One durable incident episode; the existing Gate log/status carries signals."""

    __tablename__ = "watch_voice_incidents"
    __table_args__ = (
        Index("uq_watch_voice_incident_open", "workspace_key", "account_id", unique=True,
              postgresql_where=text("recovered_at IS NULL"),
              sqlite_where=text("recovered_at IS NULL")),
        Index("ix_watch_voice_incident_scope_time", "workspace_key", "account_id", "opened_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_key: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(255))
    codes_json: Mapped[str] = mapped_column(Text)
    attempted_codes_json: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    log_attempt_count: Mapped[int] = mapped_column(Integer, default=0)


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

