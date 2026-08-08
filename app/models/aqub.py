"""AQUB provider-bound installation models.

Raw Telegram identifiers are intentionally confined to Gate persistence. They
must not be copied into AQUB envelopes, logs, configuration variables, or
tracked release constants.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
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
