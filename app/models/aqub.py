"""AQUB provider-bound installation models.

Raw Telegram identifiers are intentionally confined to Gate persistence. They
must not be copied into AQUB envelopes, logs, configuration variables, or
tracked release constants.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AQUBInstallationBinding(Base):
    """Single durable provider binding for one AQUB Telegram installation."""

    __tablename__ = "aqub_installation_bindings"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    bot_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    group_chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    alpha_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AQUBPhotoArchive(Base):
    """Dedicated resumable ledger and bounded raw-byte archive for AQUB photos."""

    __tablename__ = "aqub_photo_archives"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_aqub_photo_archives_receipt"),
        CheckConstraint(
            "status IN ('pending_download', 'processing', 'retry_scheduled', "
            "'archived', 'purged', 'dead_letter')",
            name="ck_aqub_photo_archives_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 5 "
            "AND lease_generation = attempt_count",
            name="ck_aqub_photo_archives_attempts",
        ),
        CheckConstraint(
            "source_payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_aqub_photo_archives_source_digest",
        ),
        CheckConstraint(
            "declared_size_bytes IS NULL OR "
            "(declared_size_bytes > 0 AND declared_size_bytes <= 20000000)",
            name="ck_aqub_photo_archives_declared_size",
        ),
        CheckConstraint(
            "(status = 'dead_letter' "
            "AND file_unique_digest IS NULL "
            "AND width IS NULL AND height IS NULL "
            "AND declared_size_bytes IS NULL) OR ("
            "file_unique_digest IS NOT NULL "
            "AND file_unique_digest ~ '^[0-9a-f]{64}$' "
            "AND width IS NOT NULL AND width BETWEEN 1 AND 100000 "
            "AND height IS NOT NULL AND height BETWEEN 1 AND 100000)",
            name="ck_aqub_photo_archives_photo_identity",
        ),
        CheckConstraint(
            "(last_error_code IS NULL AND last_error_at IS NULL) OR ("
            "last_error_code IS NOT NULL "
            "AND last_error_code ~ '^[a-z][a-z0-9_]{0,127}$' "
            "AND last_error_at IS NOT NULL)",
            name="ck_aqub_photo_archives_error_pair",
        ),
        CheckConstraint(
            "(status = 'pending_download' "
            "AND attempt_count = 0 AND available_at IS NOT NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NULL) "
            "OR (status = 'processing' "
            "AND attempt_count BETWEEN 1 AND 5 "
            "AND available_at IS NULL "
            "AND lease_token IS NOT NULL AND locked_at IS NOT NULL "
            "AND last_error_code IS NULL) "
            "OR (status = 'retry_scheduled' "
            "AND attempt_count BETWEEN 1 AND 4 "
            "AND available_at IS NOT NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NOT NULL) "
            "OR (status IN ('archived', 'purged') "
            "AND attempt_count BETWEEN 1 AND 5 "
            "AND available_at IS NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NULL) "
            "OR (status = 'dead_letter' "
            "AND available_at IS NULL "
            "AND lease_token IS NULL AND locked_at IS NULL "
            "AND last_error_code IS NOT NULL)",
            name="ck_aqub_photo_archives_lease_state",
        ),
        CheckConstraint(
            "(status IN ('pending_download', 'processing', 'retry_scheduled') "
            "AND raw_data IS NULL AND raw_sha256 IS NULL "
            "AND raw_size_bytes IS NULL AND raw_media_type IS NULL "
            "AND archived_at IS NULL AND purge_at IS NULL "
            "AND raw_purged_at IS NULL) "
            "OR (status = 'archived' "
            "AND raw_data IS NOT NULL AND raw_sha256 IS NOT NULL "
            "AND raw_sha256 ~ '^[0-9a-f]{64}$' "
            "AND raw_size_bytes IS NOT NULL "
            "AND raw_size_bytes BETWEEN 1 AND 20000000 "
            "AND raw_size_bytes = octet_length(raw_data) "
            "AND raw_media_type IS NOT NULL "
            "AND raw_media_type IN ('image/jpeg', 'image/png', 'image/webp') "
            "AND archived_at IS NOT NULL AND purge_at IS NOT NULL "
            "AND purge_at = archived_at + interval '7 days' "
            "AND raw_purged_at IS NULL) "
            "OR (status = 'purged' "
            "AND raw_data IS NULL AND raw_sha256 IS NOT NULL "
            "AND raw_sha256 ~ '^[0-9a-f]{64}$' "
            "AND raw_size_bytes IS NOT NULL "
            "AND raw_size_bytes BETWEEN 1 AND 20000000 "
            "AND raw_media_type IS NOT NULL "
            "AND raw_media_type IN ('image/jpeg', 'image/png', 'image/webp') "
            "AND archived_at IS NOT NULL AND purge_at IS NOT NULL "
            "AND purge_at = archived_at + interval '7 days' "
            "AND raw_purged_at IS NOT NULL AND raw_purged_at >= purge_at) "
            "OR (status = 'dead_letter' "
            "AND raw_data IS NULL AND raw_sha256 IS NULL "
            "AND raw_size_bytes IS NULL AND raw_media_type IS NULL "
            "AND archived_at IS NULL AND purge_at IS NULL "
            "AND raw_purged_at IS NULL AND last_error_code IS NOT NULL)",
            name="ck_aqub_photo_archives_raw_state",
        ),
        Index(
            "ix_aqub_photo_archives_due",
            "available_at",
            "created_at",
            "id",
            postgresql_where=text(
                "status IN ('pending_download', 'retry_scheduled') "
                "AND attempt_count < 5"
            ),
        ),
        Index(
            "ix_aqub_photo_archives_stale",
            "locked_at",
            "id",
            postgresql_where=text("status = 'processing'"),
        ),
        Index(
            "ix_aqub_photo_archives_purge",
            "purge_at",
            "id",
            postgresql_where=text("status = 'archived'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("telegram_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    binding_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("aqub_installation_bindings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_unique_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    declared_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending_download'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, deferred=True
    )
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
