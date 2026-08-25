"""AQUB provider-bound installation models.

Raw Telegram identifiers are intentionally confined to Gate persistence. They
must not be copied into AQUB envelopes, logs, configuration variables, or
tracked release constants.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class AQUBGeneralRoute(Base):
    """Default-inactive exact group+actor route for one General Cube."""

    __tablename__ = "aqub_general_routes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('hold','active','retired')",
            name="ck_aqub_general_route_status",
        ),
        CheckConstraint(
            "(status = 'hold' AND activated_at IS NULL AND retired_at IS NULL) OR "
            "(status = 'active' AND activated_at IS NOT NULL AND retired_at IS NULL) OR "
            "(status = 'retired' AND retired_at IS NOT NULL)",
            name="ck_aqub_general_route_lifecycle",
        ),
        Index(
            "uq_aqub_general_route_live_group",
            "bot_key",
            "group_chat_id",
            unique=True,
            postgresql_where=text("status <> 'retired'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    bot_key: Mapped[str] = mapped_column(String(64), nullable=False)
    group_chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'hold'"))
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AQUBFoodMedia(Base):
    """Owner-only raw Food media and its replayable normalized analysis."""

    __tablename__ = "aqub_food_media"
    __table_args__ = (
        CheckConstraint(
            "content_kind IN ('photo','voice')",
            name="ck_aqub_food_media_kind",
        ),
        CheckConstraint(
            "status IN ('queued','processing','processed','failed')",
            name="ck_aqub_food_media_status",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 3",
            name="ck_aqub_food_media_attempts",
        ),
        CheckConstraint(
            "char_length(source_sha256) = 64 OR source_sha256 IS NULL",
            name="ck_aqub_food_media_sha",
        ),
        Index(
            "ix_aqub_food_media_raw_purge",
            "raw_purge_after",
            postgresql_where=text("raw_data IS NOT NULL"),
        ),
        Index(
            "ix_aqub_food_media_worker",
            "status",
            "final_sent_at",
            "processing_started_at",
            postgresql_where=text("final_sent_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    content_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claim_token: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    processing_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    raw_purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    provider_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_request_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
