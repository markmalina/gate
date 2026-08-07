"""Add durable owner-only Wedding Beta reply ledger.

Revision ID: 0088_beta_wedding_replies
Revises: 0087_become_pilot_slice3_learner
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0088_beta_wedding_replies"
down_revision: str | None = "0087_become_pilot_slice3_learner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_space_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_text", sa.Text(), nullable=True),
        sa.Column("candidate_sha256", sa.String(64), nullable=True),
        sa.Column("result_code", sa.String(128), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_space_reply_binding_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["beta_space_events.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "state IN ('generating','sending','sent','unknown')",
            name="ck_beta_space_reply_state",
        ),
        sa.CheckConstraint(
            "char_length(request_sha256) = 64",
            name="ck_beta_space_reply_request_sha",
        ),
        sa.CheckConstraint(
            "(state = 'generating' AND candidate_text IS NULL "
            "AND candidate_sha256 IS NULL AND provider_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(state = 'sending' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL AND provider_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(state = 'sent' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL "
            "AND provider_message_id IS NOT NULL "
            "AND settled_at IS NOT NULL) OR "
            "(state = 'unknown' AND candidate_text IS NOT NULL "
            "AND candidate_sha256 IS NOT NULL "
            "AND provider_message_id IS NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_beta_space_reply_settlement",
        ),
        sa.UniqueConstraint(
            "event_id",
            name="uq_beta_space_reply_event",
        ),
        sa.UniqueConstraint(
            "request_sha256",
            name="uq_beta_space_reply_request",
        ),
    )
    op.create_index(
        "ix_beta_space_reply_scope_created",
        "beta_space_replies",
        ["workspace_id", "binding_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    evidence = connection.execute(
        sa.text("SELECT count(*) FROM beta_space_replies")
    ).scalar_one()
    if evidence:
        raise RuntimeError("beta_space_reply_evidence_blocks_downgrade")
    op.drop_index(
        "ix_beta_space_reply_scope_created",
        table_name="beta_space_replies",
    )
    op.drop_table("beta_space_replies")
