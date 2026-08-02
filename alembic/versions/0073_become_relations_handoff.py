"""Add provider-disabled BECOME to Relations consent handoffs.

Revision ID: 0073_become_relations_handoff
Revises: 0072_become_controlled_consent
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073_become_relations_handoff"
down_revision: str | None = "0072_become_controlled_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_relations_consent_handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("handoff_key", sa.String(length=128), nullable=False),
        sa.Column("command_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'relations.controlled-consent-command@1'",
            name="ck_become_relations_handoff_schema",
        ),
        sa.CheckConstraint(
            "status = 'pending_delivery'",
            name="ck_become_relations_handoff_status",
        ),
        sa.CheckConstraint(
            "char_length(command_digest) = 64",
            name="ck_become_relations_handoff_digest",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_relations_handoff_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["consent_receipt_id"],
            ["become_controlled_consent_receipts.id"],
            name="fk_become_relations_handoff_consent",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consent_receipt_id",
            name="uq_become_relations_handoff_consent",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "handoff_key",
            name="uq_become_relations_handoff_workspace_key",
        ),
    )
    op.create_index(
        "ix_become_relations_handoff_workspace_created",
        "become_relations_consent_handoffs",
        ["workspace_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_relations_handoff_workspace_created",
        table_name="become_relations_consent_handoffs",
    )
    op.drop_table("become_relations_consent_handoffs")
