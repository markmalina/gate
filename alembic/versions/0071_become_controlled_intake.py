"""Add durable content-free BECOME controlled-intake receipts.

Revision ID: 0071_become_controlled_intake
Revises: 0070_long_call_claim_v13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071_become_controlled_intake"
down_revision: str | None = "0070_long_call_claim_v13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_controlled_intake_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_key", sa.String(length=128), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("connector_key", sa.String(length=128), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("installation_ref", sa.String(length=128), nullable=False),
        sa.Column("subject_ref", sa.String(length=128), nullable=False),
        sa.Column("update_ref", sa.String(length=128), nullable=False),
        sa.Column("group_state", sa.String(length=32), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_name = 'gate.become-controlled-intake@1'",
            name="ck_become_intake_schema",
        ),
        sa.CheckConstraint(
            "connector_key = 'telegram_become_group'",
            name="ck_become_intake_connector",
        ),
        sa.CheckConstraint(
            "intent = 'registration_confirmed'",
            name="ck_become_intake_intent",
        ),
        sa.CheckConstraint(
            "group_state = 'owner_only' AND member_count = 2",
            name="ck_become_intake_owner_only",
        ),
        sa.CheckConstraint(
            "char_length(receipt_digest) = 64",
            name="ck_become_intake_digest",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_intake_installation_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_receipt_id"],
            ["telegram_receipts.id"],
            name="fk_become_intake_source_receipt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_receipt_id",
            "intent",
            name="uq_become_intake_source_intent",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "receipt_key",
            name="uq_become_intake_workspace_key",
        ),
    )
    op.create_index(
        "ix_become_intake_installation_created",
        "become_controlled_intake_receipts",
        ["installation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_intake_installation_created",
        table_name="become_controlled_intake_receipts",
    )
    op.drop_table("become_controlled_intake_receipts")
