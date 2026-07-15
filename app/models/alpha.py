"""Durable, workspace-scoped Alpha installation and delivery capability models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base


class AgentInstallationModel(Base):
    """One Alpha deployment approved for one connector-owned dialog."""

    __tablename__ = "agent_installations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connector_id", "workspace_id"],
            ["transport_connectors.id", "transport_connectors.workspace_id"],
            name="fk_agent_installations_connector_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_agent_installations_dialog_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["deployment_id", "workspace_id"],
            ["agent_deployments.id", "agent_deployments.workspace_id"],
            name="fk_agent_installations_deployment_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_installations_revision_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "installation_key", name="uq_agent_installations_key"),
        UniqueConstraint(
            "workspace_id", "connector_id", "dialog_id", name="uq_agent_installations_dialog"
        ),
        UniqueConstraint("id", "workspace_id", name="uq_agent_installations_id_workspace"),
        CheckConstraint(
            "state IN ('discovered', 'pending_approval', 'active', 'paused', 'revoked')",
            name="ck_agent_installations_state",
        ),
        CheckConstraint(
            "state NOT IN ('active', 'paused') OR "
            "(deployment_id IS NOT NULL AND instruction_revision_id IS NOT NULL)",
            name="ck_agent_installations_active_binding",
        ),
        CheckConstraint("max_turns_per_window > 0", name="ck_agent_installations_turn_budget"),
        CheckConstraint("rate_window_seconds > 0", name="ck_agent_installations_rate_window"),
        CheckConstraint("max_tokens_per_turn > 0", name="ck_agent_installations_token_budget"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    instruction_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    installation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dialog_label: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'discovered'")
    )
    max_turns_per_window: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    rate_window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )
    max_tokens_per_turn: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2048")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlphaUsageRecordModel(Base):
    """Idempotent per-trigger turn reservation and token accounting."""

    __tablename__ = "alpha_usage_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_alpha_usage_installation_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["trigger_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_alpha_usage_trigger_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "usage_key", name="uq_alpha_usage_key"),
        UniqueConstraint(
            "installation_id", "trigger_message_id", name="uq_alpha_usage_trigger"
        ),
        UniqueConstraint("id", "workspace_id", name="uq_alpha_usage_id_workspace"),
        CheckConstraint(
            "state IN ('reserved', 'recorded', 'released')",
            name="ck_alpha_usage_state",
        ),
        CheckConstraint("reserved_tokens > 0", name="ck_alpha_usage_reserved_tokens"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_alpha_usage_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_alpha_usage_output_tokens"
        ),
        Index("ix_alpha_usage_installation_created", "installation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    usage_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'reserved'")
    )
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeliveryGrantModel(Base):
    """Short-lived one-use capability bound to one exact Alpha candidate."""

    __tablename__ = "delivery_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_delivery_grants_installation_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["trigger_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_delivery_grants_trigger_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "grant_key", name="uq_delivery_grants_key"),
        UniqueConstraint(
            "installation_id",
            "trigger_message_id",
            "candidate_sha256",
            name="uq_delivery_grants_candidate",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_delivery_grants_id_workspace"),
        CheckConstraint(
            "char_length(candidate_sha256) = 64", name="ck_delivery_grants_candidate_sha"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grant_key: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentSendAttemptModel(Base):
    """Agent-only send lifecycle kept separate from human-confirmed attempts."""

    __tablename__ = "agent_send_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["grant_id", "workspace_id"],
            ["delivery_grants.id", "delivery_grants.workspace_id"],
            name="fk_agent_send_attempts_grant_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint("grant_id", name="uq_agent_send_attempts_grant"),
        CheckConstraint(
            "state IN ('reserved', 'sending', 'sent', 'failed', 'unknown', 'blocked')",
            name="ck_agent_send_attempts_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'reserved'")
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
