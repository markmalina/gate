"""Add workspace-scoped Agent Kernel persistence.

Revision ID: 0017_agent_kernel
Revises: 0016_canonical_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_agent_kernel"
down_revision: str | None = "0016_canonical_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only new tables; no existing row or writer is changed."""

    op.create_table(
        "agent_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("agent_key IN ('alpha_group', 'oko')", name="ck_agent_definitions_key"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_agent_definitions_id_workspace"),
        sa.UniqueConstraint("workspace_id", "agent_key", name="uq_agent_definitions_workspace_key"),
    )
    op.create_table(
        "instruction_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_key", sa.String(length=128), nullable=False),
        sa.Column("source_repository", sa.String(length=255), nullable=False),
        sa.Column("source_path", sa.String(length=512), nullable=False),
        sa.Column("source_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "validation_state", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("char_length(content_sha256) = 64", name="ck_instruction_content_sha"),
        sa.CheckConstraint("char_length(source_commit_sha) = 40", name="ck_instruction_git_sha"),
        sa.CheckConstraint(
            "validation_state IN ('pending', 'validated', 'rejected')",
            name="ck_instruction_revisions_validation_state",
        ),
        sa.ForeignKeyConstraint(
            ["agent_definition_id", "workspace_id"],
            ["agent_definitions.id", "agent_definitions.workspace_id"],
            name="fk_instruction_revisions_agent_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_instruction_revisions_id_workspace"),
        sa.UniqueConstraint(
            "workspace_id",
            "agent_definition_id",
            "revision_key",
            name="uq_instruction_revisions_agent_key",
        ),
    )
    op.create_table(
        "agent_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instruction_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_key", sa.String(length=128), nullable=False),
        sa.Column("runtime_profile", sa.String(length=128), nullable=False),
        sa.Column("model_profile", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="staged", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('staged', 'active', 'paused')", name="ck_agent_deployments_state"
        ),
        sa.ForeignKeyConstraint(
            ["agent_definition_id", "workspace_id"],
            ["agent_definitions.id", "agent_definitions.workspace_id"],
            name="fk_agent_deployments_agent_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_deployments_revision_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_agent_deployments_id_workspace"),
        sa.UniqueConstraint("workspace_id", "deployment_key", name="uq_agent_deployments_key"),
    )
    op.create_table(
        "agent_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instruction_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('active', 'paused', 'completed', 'handoff_required')",
            name="ck_agent_sessions_state",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "workspace_id"],
            ["agent_deployments.id", "agent_deployments.workspace_id"],
            name="fk_agent_sessions_deployment_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dialog_id", "workspace_id"],
            ["dialogs.id", "dialogs.workspace_id"],
            name="fk_agent_sessions_dialog_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_sessions_revision_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_agent_sessions_id_workspace"),
        sa.UniqueConstraint("workspace_id", "session_key", name="uq_agent_sessions_key"),
    )
    op.create_table(
        "agent_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instruction_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("result_kind", sa.String(length=32), nullable=True),
        sa.Column("result_code", sa.String(length=128), nullable=True),
        sa.Column("candidate_text", sa.Text(), nullable=True),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'proposed', 'failed')", name="ck_agent_turns_status"
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR result_kind IN ('reply', 'handoff', 'no_reply')",
            name="ck_agent_turns_result_kind",
        ),
        sa.CheckConstraint(
            "(result_kind = 'reply' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL) OR "
            "(result_kind IN ('handoff', 'no_reply') AND candidate_text IS NULL "
            "AND candidate_sha256 IS NULL) OR result_kind IS NULL",
            name="ck_agent_turns_result_payload",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_agent_turns_revision_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "workspace_id"],
            ["agent_sessions.id", "agent_sessions.workspace_id"],
            name="fk_agent_turns_session_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_message_id", "workspace_id"],
            ["canonical_messages.id", "canonical_messages.workspace_id"],
            name="fk_agent_turns_trigger_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "trigger_message_id", name="uq_agent_turns_trigger"),
    )


def downgrade() -> None:
    """Remove only the isolated Agent Kernel tables in reverse dependency order."""

    op.drop_table("agent_turns")
    op.drop_table("agent_sessions")
    op.drop_table("agent_deployments")
    op.drop_table("instruction_revisions")
    op.drop_table("agent_definitions")
