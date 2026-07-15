"""Workspace-scoped persistence models for the versioned Agent Kernel."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.domain import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class AgentDefinitionModel(Base):
    """Stable workspace-owned product agent identity."""

    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_key", name="uq_agent_definitions_workspace_key"),
        UniqueConstraint("id", "workspace_id", name="uq_agent_definitions_id_workspace"),
        CheckConstraint("agent_key IN ('alpha_group', 'oko')", name="ck_agent_definitions_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InstructionRevisionModel(Base):
    """Immutable Git and content identity of one reviewed instruction bundle."""

    __tablename__ = "instruction_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_definition_id", "workspace_id"],
            ["agent_definitions.id", "agent_definitions.workspace_id"],
            name="fk_instruction_revisions_agent_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "agent_definition_id",
            "revision_key",
            name="uq_instruction_revisions_agent_key",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_instruction_revisions_id_workspace"),
        CheckConstraint(
            "validation_state IN ('pending', 'validated', 'rejected')",
            name="ck_instruction_revisions_validation_state",
        ),
        CheckConstraint("char_length(source_commit_sha) = 40", name="ck_instruction_git_sha"),
        CheckConstraint("char_length(content_sha256) = 64", name="ck_instruction_content_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    source_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentDeploymentModel(Base):
    """Runtime/model/policy profile pinned to one validated revision."""

    __tablename__ = "agent_deployments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_definition_id", "workspace_id"],
            ["agent_definitions.id", "agent_definitions.workspace_id"],
            name="fk_agent_deployments_agent_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_deployments_revision_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "deployment_key", name="uq_agent_deployments_key"),
        UniqueConstraint("id", "workspace_id", name="uq_agent_deployments_id_workspace"),
        CheckConstraint(
            "state IN ('staged', 'active', 'paused')", name="ck_agent_deployments_state"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    instruction_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deployment_key: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_profile: Mapped[str] = mapped_column(String(128), nullable=False)
    model_profile: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'staged'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentSessionModel(Base):
    """Dialog-scoped state pinned to one deployment and instruction revision."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_agent_sessions_dialog_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["deployment_id", "workspace_id"],
            ["agent_deployments.id", "agent_deployments.workspace_id"],
            name="fk_agent_sessions_deployment_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_sessions_revision_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "session_key", name="uq_agent_sessions_key"),
        UniqueConstraint("id", "workspace_id", name="uq_agent_sessions_id_workspace"),
        CheckConstraint(
            "state IN ('active', 'paused', 'completed', 'handoff_required')",
            name="ck_agent_sessions_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dialog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    instruction_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentTurnModel(Base):
    """Idempotent trigger and structured proposal with no delivery authority."""

    __tablename__ = "agent_turns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "workspace_id"],
            ["agent_sessions.id", "agent_sessions.workspace_id"],
            name="fk_agent_turns_session_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["trigger_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_agent_turns_trigger_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_turns_revision_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("session_id", "trigger_message_id", name="uq_agent_turns_trigger"),
        CheckConstraint(
            "status IN ('queued', 'running', 'proposed', 'failed')", name="ck_agent_turns_status"
        ),
        CheckConstraint(
            "result_kind IS NULL OR result_kind IN ('reply', 'handoff', 'no_reply')",
            name="ck_agent_turns_result_kind",
        ),
        CheckConstraint(
            "(result_kind = 'reply' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL) OR "
            "(result_kind IN ('handoff', 'no_reply') AND candidate_text IS NULL "
            "AND candidate_sha256 IS NULL) OR result_kind IS NULL",
            name="ck_agent_turns_result_payload",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    instruction_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    context_message_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'queued'"))
    result_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
