"""Gate SQLAlchemy domain models."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TELEGRAM_MARK_RU_ACCOUNT_ID = uuid.UUID("0d8e025f-323d-5e86-a44c-a620f690ae4f")
TELEGRAM_MARK_BY_ACCOUNT_ID = uuid.UUID("0f26e375-e470-5aa2-9a4e-c2f9ad9d24b2")
ALPHA_PERSONAL_PROJECT_ID = uuid.UUID("9d2010e7-9583-5aa5-b7a9-ce73ac5e22eb")
OKO_PROJECT_ID = uuid.UUID("f2659ed6-9a1d-5f72-8bf1-fe45420cfef7")
ALPHA_MAYAKI_PROJECT_ID = uuid.UUID("329d252f-f839-5ddd-963e-50b82469cf39")
UNCLASSIFIED_PROJECT_ID = uuid.UUID("e12d3d85-6c5f-5f8a-a2fa-ac35f7ac80c0")

DEFAULT_ACCOUNT_ID = TELEGRAM_MARK_RU_ACCOUNT_ID
DEFAULT_PROJECT_ID = UNCLASSIFIED_PROJECT_ID


class Base(DeclarativeBase):
    """Base class for Gate ORM models."""


class ReplyMode(StrEnum):
    """Supported Gate reply modes."""

    MANUAL = "manual"
    APPROVAL = "approval"
    AUTO_SAFE = "auto-safe"
    AUTO_FULL = "auto-full"


class MessageDirection(StrEnum):
    """Normalized message direction values."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ConversationStatus(StrEnum):
    """Conversation lifecycle states."""

    OPEN = "open"
    REPLIED = "replied"
    CLOSED = "closed"
    MUTED = "muted"


class ReplyTaskStatus(StrEnum):
    """Reply task workflow states."""

    NEEDS_REPLY = "needs_reply"
    REPLIED = "replied"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"


class ReplyDraftStatus(StrEnum):
    """Reply draft lifecycle states."""

    GENERATED = "generated"
    EDITED = "edited"
    REJECTED = "rejected"
    APPROVED = "approved"


class SendAttemptStatus(StrEnum):
    """Externally visible delivery attempt states."""

    RESERVED = "reserved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class TelegramReceiptStatus(StrEnum):
    """Durable Telegram receipt lifecycle states."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    DEAD_LETTER = "dead_letter"


class TelegramOutboxStatus(StrEnum):
    """Transactional outbox delivery states."""

    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"


class Bot(Base):
    """Telegram bot adapter configuration without committed token values."""

    __tablename__ = "bots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    token_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    routes: Mapped[list["Route"]] = relationship(back_populates="bot")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="bot")


class Account(Base):
    """Telegram account context owned by a person, project, or client."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    phone_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_type: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True
    )
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reply_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReplyMode.MANUAL.value
    )
    audit_log_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    read_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    linked_project: Mapped["Project | None"] = relationship(back_populates="accounts")
    routes: Mapped[list["Route"]] = relationship(back_populates="account")


class Project(Base):
    """Internal project or agent receiving routed Gate events."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    adapter_type: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    accounts: Mapped[list[Account]] = relationship(back_populates="linked_project")
    routes: Mapped[list["Route"]] = relationship(back_populates="project")


class Route(Base):
    """Routing rule from Telegram event metadata to a project."""

    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("bot_id", "account_id", "chat_id", "event_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bots.id"), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    reply_mode_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    bot: Mapped[Bot | None] = relationship(back_populates="routes")
    account: Mapped[Account | None] = relationship(back_populates="routes")
    project: Mapped[Project] = relationship(back_populates="routes")


class GateEvent(Base):
    """Immutable-ish communication event received by Gate."""

    __tablename__ = "gate_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    business_connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TelegramUpdate(Base):
    """Durable raw Telegram update receipt used for idempotency."""

    __tablename__ = "telegram_updates"
    __table_args__ = (
        UniqueConstraint("bot_id", "update_id", name="uq_telegram_updates_bot_update_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="processed")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Person(Base):
    """A human represented by one or more platform identities."""

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PersonIdentity(Base):
    """Platform identity connecting a person to a Gate account context."""

    __tablename__ = "person_identities"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "platform",
            "platform_user_id",
            name="uq_person_identities_account_platform_user",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="telegram")
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PersonProjectRelation(Base):
    """Historical relationship between a person and a project."""

    __tablename__ = "person_project_relations"
    __table_args__ = (
        Index(
            "ix_person_project_relations_active",
            "person_id",
            "project_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TelegramBusinessConnection(Base):
    """Telegram Business connection assigned to one Gate account."""

    __tablename__ = "telegram_business_connections"
    __table_args__ = (
        UniqueConstraint(
            "business_connection_id",
            name="uq_telegram_business_connections_business_connection_id",
        ),
        Index(
            "uq_telegram_business_connections_enabled_account",
            "account_id",
            unique=True,
            postgresql_where=text("is_enabled"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )
    business_connection_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_update_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    """Normalized inbound or outbound communication message."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "canonical_message_id",
            name="uq_messages_canonical_message_id",
        ),
        Index("ix_messages_bot_chat_created_at", "bot_id", "chat_id", "created_at"),
        Index("ix_messages_direction_created_at", "direction", "created_at"),
        Index(
            "ix_messages_telegram_identity_lookup",
            "account_id",
            text("coalesce(business_connection_id, '')"),
            "chat_id",
            "telegram_message_id",
            postgresql_where=text("telegram_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
        default=lambda: DEFAULT_ACCOUNT_ID,
        server_default=text(f"'{DEFAULT_ACCOUNT_ID}'::uuid"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        default=lambda: DEFAULT_PROJECT_ID,
        server_default=text(f"'{DEFAULT_PROJECT_ID}'::uuid"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id"), nullable=True, index=True
    )
    context_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_context_bindings.id"), nullable=True, index=True
    )
    gate_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gate_events.id"), nullable=True, index=True
    )
    canonical_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("canonical_messages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    business_connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    """State for a Telegram conversation grouped by routing context."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_bot_chat_updated_at", "bot_id", "chat_id", "updated_at"),
        Index("ix_conversations_requires_reply", "requires_reply", "updated_at"),
        Index(
            "uq_conversations_context",
            "bot_id",
            "account_id",
            "project_id",
            "chat_id",
            text("coalesce(business_connection_id, '__gate_null_business_connection__')"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id"), nullable=True, index=True
    )
    context_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_context_bindings.id"), nullable=True, index=True
    )
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    business_connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConversationStatus.OPEN.value
    )
    requires_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bot: Mapped[Bot] = relationship(back_populates="conversations")
    reply_tasks: Mapped[list["ReplyTask"]] = relationship(back_populates="conversation")
    reply_drafts: Mapped[list["ReplyDraft"]] = relationship(back_populates="conversation")


class ConversationContextBinding(Base):
    """A time-bounded project context assigned to a conversation segment."""

    __tablename__ = "conversation_context_bindings"
    __table_args__ = (
        Index(
            "ix_conversation_context_bindings_active",
            "conversation_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    effective_from_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    effective_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    context_type: Mapped[str] = mapped_column(String(64), nullable=False, default="conversation")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MessageContextAssignment(Base):
    """Persisted project assignment for one message."""

    __tablename__ = "message_context_assignments"
    __table_args__ = (
        Index(
            "ix_message_context_assignments_message_created",
            "message_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    context_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_context_bindings.id"), nullable=True
    )
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplyTask(Base):
    """Task representing an inbound message awaiting Mark's reply."""

    __tablename__ = "reply_tasks"
    __table_args__ = (
        UniqueConstraint("trigger_message_id", name="uq_reply_tasks_trigger_message_id"),
        Index("ix_reply_tasks_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
        default=lambda: DEFAULT_ACCOUNT_ID,
        server_default=text(f"'{DEFAULT_ACCOUNT_ID}'::uuid"),
        index=True,
        deferred=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        default=lambda: DEFAULT_PROJECT_ID,
        server_default=text(f"'{DEFAULT_PROJECT_ID}'::uuid"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id"), nullable=True, index=True
    )
    context_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_context_bindings.id"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    trigger_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReplyTaskStatus.NEEDS_REPLY.value
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="reply_tasks")
    trigger_message: Mapped[Message] = relationship()
    reply_drafts: Mapped[list["ReplyDraft"]] = relationship(back_populates="reply_task")


class ReplyDraft(Base):
    """AI-suggested draft text for a reply task, never sent directly."""

    __tablename__ = "reply_drafts"
    __table_args__ = (
        Index("ix_reply_drafts_status_created_at", "status", "created_at"),
        Index("ix_reply_drafts_task_created_at", "reply_task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
        default=lambda: DEFAULT_ACCOUNT_ID,
        server_default=text(f"'{DEFAULT_ACCOUNT_ID}'::uuid"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        default=lambda: DEFAULT_PROJECT_ID,
        server_default=text(f"'{DEFAULT_PROJECT_ID}'::uuid"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id"), nullable=True, index=True
    )
    context_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_context_bindings.id"), nullable=True, index=True
    )
    reply_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_tasks.id"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    trigger_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReplyDraftStatus.GENERATED.value
    )
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reply_task: Mapped[ReplyTask] = relationship(back_populates="reply_drafts")
    conversation: Mapped[Conversation] = relationship(back_populates="reply_drafts")
    trigger_message: Mapped[Message] = relationship()


class SendAttempt(Base):
    """Idempotent reservation and outcome for one explicitly approved draft send."""

    __tablename__ = "send_attempts"
    __table_args__ = (
        UniqueConstraint("reply_draft_id", name="uq_send_attempts_reply_draft_id"),
        Index("ix_send_attempts_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reply_draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_drafts.id"), nullable=False, index=True
    )
    reply_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_tasks.id"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SendAttemptStatus.RESERVED.value
    )
    account_label: Mapped[str] = mapped_column(String(32), nullable=False)
    target_chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    business_connection_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TelegramReceipt(Base):
    """Durable deduplicated Telegram update receipt."""

    __tablename__ = "telegram_receipts"
    __table_args__ = (
        UniqueConstraint(
            "bot_key",
            "update_id",
            name="uq_telegram_receipts_bot_update",
        ),
        Index(
            "ix_telegram_receipts_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    bot_key: Mapped[str] = mapped_column(String(255), nullable=False)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TelegramReceiptStatus.RECEIVED.value,
        server_default=text("'received'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TelegramVoiceArchive(Base):
    """Durable original audio for one scoped Telegram Business voice receipt."""

    __tablename__ = "telegram_voice_archives"
    __table_args__ = (
        UniqueConstraint(
            "receipt_id",
            name="uq_telegram_voice_archives_receipt_id",
        ),
        UniqueConstraint(
            "account_id",
            "business_connection_id",
            "chat_id",
            "telegram_message_id",
            name="uq_telegram_voice_archives_message_identity",
        ),
        Index(
            "ix_telegram_voice_archives_account_received",
            "account_id",
            "received_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    business_connection_id: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    transcription_status: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_language: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    transcription_model: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    transcription_reason_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    transcribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    transcription_recovery_authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    summary_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class DropboxCallInboxState(Base):
    """Durable Dropbox delta cursor and retry state without file payloads."""

    __tablename__ = "dropbox_call_inbox_states"

    account_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfill_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    webhook_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class PhoneCallRecording(Base):
    """Bounded original mobile call recording and its automatic transcript."""

    __tablename__ = "phone_call_recordings"
    __table_args__ = (
        UniqueConstraint(
            "source_account_id",
            "source_file_id",
            "source_revision",
            name="uq_phone_call_recordings_source_revision",
        ),
        Index(
            "ix_phone_call_recordings_workspace_received",
            "workspace_key",
            "received_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_key: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="phone_call_dropbox",
        server_default=text("'phone_call_dropbox'"),
    )
    source_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcription_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="received",
        server_default=text("'received'"),
    )
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transcription_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcription_reason_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    transcribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    summary_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class TelegramOutboxEvent(Base):
    """One transactional processing event for a durable Telegram receipt."""

    __tablename__ = "telegram_outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "receipt_id",
            name="uq_telegram_outbox_events_receipt_id",
        ),
        Index(
            "ix_telegram_outbox_status_available",
            "status",
            "available_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="telegram_update_received",
        server_default=text("'telegram_update_received'"),
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TelegramOutboxStatus.PENDING.value,
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TelegramQueueIncidentRecord(Base):
    """Durable payload-free aggregate Telegram queue alert incident."""

    __tablename__ = "telegram_queue_incidents"
    __table_args__ = (
        Index(
            "ix_telegram_queue_incidents_status_observed",
            "status",
            "last_observed_at",
        ),
        Index(
            "uq_telegram_queue_incidents_single_active",
            text("(1)"),
            unique=True,
            postgresql_where=text(
                "status IN ('open', 'notified', 'acknowledged')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="open",
        server_default=text("'open'"),
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    notification_channel: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditLog(Base):
    """Audit trail for routing, approval, and future send decisions."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    bot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bots.id"), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    gate_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gate_events.id"), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
