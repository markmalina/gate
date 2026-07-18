"""Tenant-ready canonical messaging persistence models.

These models are isolated from the legacy domain module so new aggregates can follow
the target architecture without expanding the historical ORM monolith.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class Workspace(Base):
    """Explicit tenant boundary for every new aggregate root."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TransportConnector(Base):
    """Workspace-owned transport identity without a committed credential value."""

    __tablename__ = "transport_connectors"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "connector_key", name="uq_transport_connectors_workspace_key"
        ),
        UniqueConstraint("id", "workspace_id", name="uq_transport_connectors_id_workspace"),
        CheckConstraint(
            "connector_kind IN ('telegram_business', 'telegram_bot')",
            name="ck_transport_connectors_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Dialog(Base):
    """Connector-neutral dialog identity independent of project classification."""

    __tablename__ = "dialogs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connector_id", "workspace_id"],
            ["transport_connectors.id", "transport_connectors.workspace_id"],
            name="fk_dialogs_connector_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "connector_id",
            "provider_dialog_id",
            name="uq_dialogs_connector_provider",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_dialogs_id_workspace"),
        CheckConstraint(
            "dialog_kind IN ('private', 'group', 'supergroup', 'channel')",
            name="ck_dialogs_kind",
        ),
        Index("ix_dialogs_workspace_kind", "workspace_id", "dialog_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider_dialog_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dialog_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CanonicalMessage(Base):
    """Immutable normalized message fact linked to one durable receipt."""

    __tablename__ = "canonical_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_canonical_messages_dialog_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "dialog_id",
            "provider_message_id",
            name="uq_canonical_messages_dialog_provider",
        ),
        UniqueConstraint(
            "receipt_id",
            name="uq_canonical_messages_receipt_id",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_canonical_messages_id_workspace"),
        UniqueConstraint(
            "id",
            "dialog_id",
            "workspace_id",
            name="uq_canonical_messages_id_dialog_workspace",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound', 'bot_or_service', 'unknown')",
            name="ck_canonical_messages_direction",
        ),
        CheckConstraint(
            "input_origin IN ('telegram_text', 'telegram_voice_transcription')",
            name="ck_canonical_messages_input_origin",
        ),
        Index("ix_canonical_messages_dialog_occurred", "dialog_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("telegram_receipts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_sender_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    direction: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unknown'")
    )
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_origin: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'telegram_text'")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TelegramBusinessTextParityEvidence(Base):
    """Content-free proof that canonical and legacy Business text facts agree."""

    __tablename__ = "telegram_business_text_parity_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["canonical_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_business_text_parity_message_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "canonical_message_id",
            name="uq_business_text_parity_canonical_message",
        ),
        CheckConstraint(
            "state IN ('matched', 'pending', 'mismatch')",
            name="ck_business_text_parity_state",
        ),
        CheckConstraint(
            "(state = 'matched' AND mismatch_code IS NULL) OR "
            "(state = 'pending' AND mismatch_code = "
            "'telegram_business_text_parity_legacy_pending') OR "
            "(state = 'mismatch' AND mismatch_code IN ("
            "'telegram_business_text_parity_account_mismatch', "
            "'telegram_business_text_parity_direction_mismatch', "
            "'telegram_business_text_parity_dialog_mismatch', "
            "'telegram_business_text_parity_text_mismatch', "
            "'telegram_business_text_parity_reply_task_mismatch', "
            "'telegram_business_text_parity_legacy_cardinality', "
            "'telegram_business_text_parity_legacy_invalid'))",
            name="ck_business_text_parity_code_state",
        ),
        CheckConstraint(
            "observation_count > 0",
            name="ck_business_text_parity_observation_count",
        ),
        Index(
            "ix_business_text_parity_workspace_account_state",
            "workspace_id",
            "account_id",
            "state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    mismatch_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkOwnership(Base):
    """Versioned current ownership for one workspace-scoped dialog."""

    __tablename__ = "work_ownerships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_work_ownerships_dialog_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["last_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_work_ownerships_message_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("dialog_id", name="uq_work_ownerships_dialog"),
        CheckConstraint(
            "state IN ('human_managed', 'agent_managed', 'handoff_required', "
            "'paused', 'ignored', 'quarantined')",
            name="ck_work_ownerships_state",
        ),
        CheckConstraint(
            "route IN ('human_reply_task', 'agent_turn', 'agent_handoff', "
            "'ignored', 'quarantined')",
            name="ck_work_ownerships_route",
        ),
        CheckConstraint(
            "agent_key IS NULL OR agent_key IN ('alpha_group', 'oko')",
            name="ck_work_ownerships_agent_key",
        ),
        CheckConstraint("version > 0", name="ck_work_ownerships_version"),
        Index("ix_work_ownerships_workspace_state", "workspace_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    last_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    route: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
