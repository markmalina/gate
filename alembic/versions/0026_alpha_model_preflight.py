"""Add one-shot delivery-free Alpha provider preflight evidence.

Revision ID: 0026_alpha_model_preflight
Revises: 0025_resume_alpha_canary_drill
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_alpha_model_preflight"
down_revision: str | None = "0025_resume_alpha_canary_drill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create content-free, idempotent provider preflight evidence."""

    op.create_table(
        "alpha_model_preflights",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "instruction_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("preflight_key", sa.String(length=128), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'reserved'"),
            nullable=False,
        ),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("result_kind", sa.String(length=32), nullable=True),
        sa.Column("result_code", sa.String(length=128), nullable=True),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'succeeded', 'failed')",
            name="ck_alpha_model_preflight_state",
        ),
        sa.CheckConstraint(
            "reserved_tokens > 0",
            name="ck_alpha_model_preflight_reserved_tokens",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_alpha_model_preflight_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_alpha_model_preflight_output_tokens",
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR result_kind IN ('reply', 'handoff', 'no_reply')",
            name="ck_alpha_model_preflight_result_kind",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "workspace_id"],
            ["agent_deployments.id", "agent_deployments.workspace_id"],
            name="fk_alpha_preflight_deployment_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_revision_id", "workspace_id"],
            ["instruction_revisions.id", "instruction_revisions.workspace_id"],
            name="fk_alpha_preflight_revision_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "preflight_key",
            name="uq_alpha_model_preflight_key",
        ),
    )
    op.create_index(
        "ix_alpha_model_preflight_workspace_created",
        "alpha_model_preflights",
        ["workspace_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only provider preflight evidence."""

    op.drop_index(
        "ix_alpha_model_preflight_workspace_created",
        table_name="alpha_model_preflights",
    )
    op.drop_table("alpha_model_preflights")
