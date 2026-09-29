"""Shared #718/#716 durable sequence authority; no message bodies are copied."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class ReplyConnectorAdmission(Base):
    __tablename__ = "reply_connector_admission"
    __table_args__ = (
        UniqueConstraint("workspace_ref", "connector_key"),
        CheckConstraint("state IN ('healthy','blocked','reconciling')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_ref: Mapped[str] = mapped_column(String(128))
    connector_key: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="blocked")
    safe_reason: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    continuity_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    epoch_id: Mapped[UUID | None] = mapped_column(ForeignKey("reply_continuity_epochs.id"))


class ReplyDialogGuard(Base):
    __tablename__ = "reply_dialog_guards"
    __table_args__ = (
        UniqueConstraint("workspace_ref", "account_id", "provider_chat_id"),
        CheckConstraint("inbound_revision >= 0"),
        CheckConstraint("health_state IN ('healthy','blocked')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_ref: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    provider_chat_id: Mapped[str] = mapped_column(String(255))
    inbound_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    last_inbound_receipt_ref: Mapped[UUID | None] = mapped_column(
        ForeignKey("telegram_receipts.id")
    )
    health_state: Mapped[str] = mapped_column(String(32), default="healthy")
    guard_version: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplyInboundSignal(Base):
    __tablename__ = "reply_inbound_signals"
    __table_args__ = (UniqueConstraint("dialog_guard_id", "connector_key", "provider_message_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    receipt_ref: Mapped[UUID] = mapped_column(ForeignKey("telegram_receipts.id"))
    dialog_guard_id: Mapped[UUID] = mapped_column(ForeignKey("reply_dialog_guards.id"))
    connector_key: Mapped[str] = mapped_column(String(64))
    provider_message_id: Mapped[str] = mapped_column(String(128))
    payload_digest: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplySequence(Base):
    __tablename__ = "reply_sequences"
    __table_args__ = (
        CheckConstraint("state IN ('active','completed','stopped')"),
        CheckConstraint("item_count BETWEEN 2 AND 50"),
        CheckConstraint("interval_seconds = 60"),
        CheckConstraint("next_position >= 0 AND next_position <= item_count"),
        Index(
            "uq_reply_sequences_active_guard",
            "guard_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index(
            "ix_reply_sequences_active_due",
            "next_due_at",
            "id",
            postgresql_where=text("state = 'active'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    guard_id: Mapped[UUID] = mapped_column(ForeignKey("reply_dialog_guards.id"))
    workspace_ref: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reply_tasks.id"))
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    principal_subject: Mapped[str] = mapped_column(String(255))
    account_label: Mapped[str] = mapped_column(String(8))
    bot_resource_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="active")
    stop_reason: Mapped[str | None] = mapped_column(String(128))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_receipt_ref: Mapped[UUID | None] = mapped_column(ForeignKey("telegram_receipts.id"))
    queued_revision: Mapped[int] = mapped_column(BigInteger)
    item_count: Mapped[int] = mapped_column(Integer)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    next_position: Mapped[int] = mapped_column(Integer, default=0)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    canary_execution_id: Mapped[str | None] = mapped_column(String(128))
    dispatch_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    claim_token: Mapped[UUID | None] = mapped_column()
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admitted_attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("send_attempts.id"))
    approval_digest: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplySequenceItem(Base):
    __tablename__ = "reply_sequence_items"
    __table_args__ = (
        UniqueConstraint("draft_id"),
        CheckConstraint("position >= 0"),
        CheckConstraint(
            "state IN ('scheduled','reserved','sending','sent','failed','unknown','cancelled')"
        ),
        Index(
            "ix_reply_sequence_items_pending",
            "sequence_id",
            "position",
            postgresql_where=text("state IN ('scheduled','reserved','sending')"),
        ),
    )
    sequence_id: Mapped[UUID] = mapped_column(ForeignKey("reply_sequences.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("reply_drafts.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), default="scheduled")
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("send_attempts.id"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(128))
    approved_text_sha256: Mapped[str] = mapped_column(String(64))


class ReplySequenceNotification(Base):
    __tablename__ = "reply_sequence_notifications"
    __table_args__ = (
        UniqueConstraint("sequence_id", "kind"),
        CheckConstraint(
            "delivery_state IN ('pending','sending','sent','failed','unknown','blocked_route')"
        ),
        Index(
            "ix_reply_sequence_notification_due",
            "next_attempt_at",
            "id",
            postgresql_where=text("delivery_state = 'pending'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    sequence_id: Mapped[UUID] = mapped_column(ForeignKey("reply_sequences.id"))
    kind: Mapped[str] = mapped_column(String(64))
    owner_route_ref: Mapped[UUID | None] = mapped_column(
        ForeignKey("telegram_business_connections.id")
    )
    delivery_state: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_message_ref: Mapped[str | None] = mapped_column(String(128))
    safe_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReplyDispatchControl(Base):
    __tablename__ = "reply_dispatch_control"
    __table_args__ = (
        CheckConstraint("mode IN ('disabled','serial','parallel')"),
        CheckConstraint("max_inflight BETWEEN 1 AND 8"),
        CheckConstraint("account_cap BETWEEN 1 AND 8 AND bot_cap BETWEEN 1 AND 8"),
    )
    workspace_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), default="disabled")
    generation: Mapped[int] = mapped_column(BigInteger, default=0)
    max_inflight: Mapped[int] = mapped_column(Integer, default=4)
    account_cap: Mapped[int] = mapped_column(Integer, default=2)
    bot_cap: Mapped[int] = mapped_column(Integer, default=4)
    migration_complete: Mapped[bool] = mapped_column(Boolean, default=False)


class ReplyDispatchPermit(Base):
    __tablename__ = "reply_dispatch_permits"
    __table_args__ = (
        CheckConstraint("state IN ('free','preflight','dispatching','quarantined')"),
        UniqueConstraint("sequence_id"),
    )
    workspace_ref: Mapped[str] = mapped_column(
        ForeignKey("reply_dispatch_control.workspace_ref"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="free")
    sequence_id: Mapped[UUID | None] = mapped_column(ForeignKey("reply_sequences.id"))
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"))
    bot_resource_id: Mapped[str | None] = mapped_column(String(64))
    token: Mapped[UUID | None] = mapped_column()
    owner: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("send_attempts.id"))


class ReplyTransportBudget(Base):
    __tablename__ = "reply_transport_budgets"
    resource_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_indefinitely: Mapped[bool] = mapped_column(Boolean, default=False)


class ReplyCanaryScope(Base):
    __tablename__ = "reply_canary_scopes"
    __table_args__ = (
        CheckConstraint("state IN ('active','closed')"),
        CheckConstraint("max_attempts BETWEEN 1 AND 6"),
        CheckConstraint("attempts_reserved >= 0 AND attempts_reserved <= max_attempts"),
        UniqueConstraint("execution_id"),
    )
    workspace_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default="active")
    principal_subject: Mapped[str] = mapped_column(String(255))
    sequence_grants: Mapped[list[str]] = mapped_column(JSONB)
    notification_destination: Mapped[str] = mapped_column(String(255))
    notification_template: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer)
    attempts_reserved: Mapped[int] = mapped_column(Integer, default=0)
    scope_sha256: Mapped[str] = mapped_column(String(64))


class ReplyCanaryAttempt(Base):
    __tablename__ = "reply_canary_attempts"
    __table_args__ = (UniqueConstraint("workspace_ref", "attempt_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_ref: Mapped[str] = mapped_column(ForeignKey("reply_canary_scopes.workspace_ref"))
    attempt_key: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(32))
    execution_id: Mapped[str] = mapped_column(String(128))
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplyContinuityEpoch(Base):
    """Initial externally verified fleet; renewal cannot replace this identity."""

    __tablename__ = "reply_continuity_epochs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_ref: Mapped[str] = mapped_column(String(128), index=True)
    evidence_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    bot_resource_id: Mapped[str] = mapped_column(String(64))
    binding_sha256: Mapped[str] = mapped_column(String(64))
    members: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplyIngressProcess(Base):
    """Actual boot identity and completed worker observations; no provider payloads."""

    __tablename__ = "reply_ingress_processes"
    workspace_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    boot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_sha: Mapped[str] = mapped_column(String(64))
    bot_resource_id: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_gap_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workers: Mapped[dict[str, dict[str, object]]] = mapped_column(JSONB)


class ReplyIngressProjection(Base):
    """Written in the same transaction as each receipt and early stop projection."""

    __tablename__ = "reply_ingress_projections"
    receipt_id: Mapped[UUID] = mapped_column(ForeignKey("telegram_receipts.id"), primary_key=True)
    workspace_ref: Mapped[str] = mapped_column(String(128), index=True)
    boot_id: Mapped[str] = mapped_column(String(128))


class ReplyContinuityRenewal(Base):
    __tablename__ = "reply_continuity_renewals"
    __table_args__ = (UniqueConstraint("epoch_id", "connector_version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    epoch_id: Mapped[UUID] = mapped_column(ForeignKey("reply_continuity_epochs.id"))
    connector_version: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    continuity_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observation_sha256: Mapped[str] = mapped_column(String(64))
