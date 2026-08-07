"""AQUB live Telegram delivery ledger."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AQUBLiveDelivery(Base):
    """One stable AQUB delivery command and its terminal Telegram result."""

    __tablename__ = "aqub_live_deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_aqub_live_delivery_idempotency"),
        UniqueConstraint("command_id", name="uq_aqub_live_delivery_command"),
        CheckConstraint(
            "state IN ('pending','sending','sent','failed','unknown')",
            name="ck_aqub_live_delivery_state",
        ),
        CheckConstraint(
            "char_length(request_sha256) = 64",
            name="ck_aqub_live_delivery_request_sha",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    step_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
