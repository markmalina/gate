"""Persistence models for the isolated BECOME controlled intake."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BecomeControlledIntakeReceiptModel(Base):
    """One content-free receipt bound to a durable Telegram update."""

    __tablename__ = "become_controlled_intake_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_intake_installation_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "receipt_key",
            name="uq_become_intake_workspace_key",
        ),
        UniqueConstraint(
            "source_receipt_id",
            "intent",
            name="uq_become_intake_source_intent",
        ),
        CheckConstraint(
            "schema_name = 'gate.become-controlled-intake@1'",
            name="ck_become_intake_schema",
        ),
        CheckConstraint(
            "connector_key = 'telegram_become_group'",
            name="ck_become_intake_connector",
        ),
        CheckConstraint(
            "intent = 'registration_confirmed'",
            name="ck_become_intake_intent",
        ),
        CheckConstraint(
            "group_state = 'owner_only' AND member_count = 2",
            name="ck_become_intake_owner_only",
        ),
        CheckConstraint(
            "char_length(receipt_digest) = 64",
            name="ck_become_intake_digest",
        ),
        Index(
            "ix_become_intake_installation_created",
            "installation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    update_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    group_state: Mapped[str] = mapped_column(String(32), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
