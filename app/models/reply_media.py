"""Immutable, scoped image packages; bytes expire, evidence does not."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class ReplyMediaPackage(Base):
    __tablename__ = "reply_media_packages"
    __table_args__ = (
        UniqueConstraint("reply_draft_id", name="uq_reply_media_package_draft"),
        UniqueConstraint(
            "workspace_id",
            "principal_subject",
            "staging_key",
            name="uq_reply_media_package_staging",
        ),
        CheckConstraint(
            "mode IN ('photo','album','rich_photo','rich_slideshow','rich_collage')",
            name="ck_reply_media_package_mode",
        ),
        CheckConstraint("expected_message_count BETWEEN 1 AND 10", name="ck_reply_media_count"),
        Index("ix_reply_media_package_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reply_draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    staging_key: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplyMediaItem(Base):
    __tablename__ = "reply_media_items"
    __table_args__ = (
        UniqueConstraint("package_id", "ordinal", name="uq_reply_media_item_order"),
        CheckConstraint("ordinal BETWEEN 1 AND 10", name="ck_reply_media_item_ordinal"),
        CheckConstraint(
            "size_bytes > 0 AND source_size_bytes > 0", name="ck_reply_media_item_size"
        ),
        CheckConstraint("width > 0 AND height > 0", name="ck_reply_media_item_dimensions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_media_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    transform_version: Mapped[str] = mapped_column(String(64), nullable=False)
    transform: Mapped[str] = mapped_column(String(255), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False, default="")
    caption_format: Mapped[str] = mapped_column(String(16), nullable=False)
    caption_formatting_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    receipt_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ReplyMediaExecutionBudget(Base):
    __tablename__ = "reply_media_execution_budgets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "principal_subject",
            "execution_id",
            name="uq_reply_media_execution_scope",
        ),
        CheckConstraint("write_cap = 12 AND message_cap = 20", name="ck_reply_media_budget_caps"),
        CheckConstraint(
            "writes_reserved >= 0 AND writes_reserved <= write_cap AND "
            "messages_reserved >= 0 AND messages_reserved <= message_cap",
            name="ck_reply_media_budget_counters",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    write_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    message_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    writes_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplyMediaBudgetReservation(Base):
    __tablename__ = "reply_media_budget_reservations"
    __table_args__ = (
        UniqueConstraint("send_attempt_id", name="uq_reply_media_budget_attempt"),
        CheckConstraint(
            "expected_messages BETWEEN 1 AND 10", name="ck_reply_media_budget_messages"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    send_attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("send_attempts.id"), nullable=False, index=True
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_media_execution_budgets.id"), nullable=False, index=True
    )
    expected_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
