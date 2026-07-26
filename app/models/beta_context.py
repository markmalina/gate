"""Durable Gate outbox and co-located Beta context sink for the first pilot."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class BetaContextBindingModel(Base):
    """One opaque Gate installation mapped to one isolated Beta group."""

    __tablename__ = "beta_context_bindings"
    __table_args__ = (
        UniqueConstraint("installation_ref", name="uq_beta_context_binding_ref"),
        UniqueConstraint("group_id", name="uq_beta_context_binding_group"),
        CheckConstraint(
            "state IN ('active', 'paused', 'revoked')",
            name="ck_beta_context_binding_state",
        ),
    )

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_installations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    installation_ref: Mapped[str] = mapped_column(String(96), nullable=False)
    user_workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GateContextOutboxModel(Base):
    """Provider-neutral event waiting for Beta acceptance."""

    __tablename__ = "gate_context_outbox"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_gate_context_outbox_idempotency",
        ),
        UniqueConstraint(
            "installation_id",
            "gate_event_ref",
            name="uq_gate_context_outbox_event_ref",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_gate_context_outbox_direction",
        ),
        CheckConstraint(
            "kind IN ('message.inbound', 'message.outbound', 'fact', "
            "'decision', 'correction', 'tombstone', 'boundary')",
            name="ck_gate_context_outbox_kind",
        ),
        CheckConstraint(
            "state IN ('pending', 'accepted', 'dead_letter')",
            name="ck_gate_context_outbox_state",
        ),
        Index("ix_gate_context_outbox_pending", "state", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("beta_context_bindings.installation_id", ondelete="CASCADE"),
        nullable=False,
    )
    gate_event_ref: Mapped[str] = mapped_column(String(96), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    canonical_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BetaContextEventModel(Base):
    """Canonical provider-neutral Beta event accepted from Gate."""

    __tablename__ = "beta_context_events"
    __table_args__ = (
        UniqueConstraint("group_id", "sequence", name="uq_beta_context_group_sequence"),
        UniqueConstraint(
            "installation_id",
            "source_event_ref",
            name="uq_beta_context_source_event",
        ),
        UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_beta_context_idempotency",
        ),
        Index("ix_beta_context_group_sequence", "group_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("beta_context_bindings.installation_id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_event_ref: Mapped[str] = mapped_column(String(96), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class BetaContextCheckpointStateModel(Base):
    """Acceptance and manual export cursors remain independent."""

    __tablename__ = "beta_context_checkpoint_state"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("beta_context_bindings.installation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    last_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_exported_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
