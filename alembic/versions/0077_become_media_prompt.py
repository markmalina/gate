"""Add one-shot BECOME media prompt delivery receipt.

Revision ID: 0077_become_media_prompt
Revises: 0076_become_media_intake
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_become_media_prompt"
down_revision: str | None = "0076_become_media_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "become_media_prompts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "consent_receipt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "relations_handoff_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("prompt_key", sa.String(length=128), nullable=False),
        sa.Column("schema_name", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("prompt_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_message_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
            "schema_name = 'gate.become-media-prompt@1'",
            name="ck_become_media_prompt_schema",
        ),
        sa.CheckConstraint(
            "state IN ('sending', 'sent', 'failed', 'blocked', 'unknown')",
            name="ck_become_media_prompt_state",
        ),
        sa.CheckConstraint(
            "char_length(prompt_digest) = 64",
            name="ck_become_media_prompt_digest",
        ),
        sa.CheckConstraint(
            "("
            "state = 'sending' "
            "AND provider_message_id IS NULL "
            "AND error_code IS NULL "
            "AND sent_at IS NULL"
            ") OR ("
            "state = 'sent' "
            "AND provider_message_id IS NOT NULL "
            "AND error_code IS NULL "
            "AND sent_at IS NOT NULL"
            ") OR ("
            "state IN ('failed', 'blocked', 'unknown') "
            "AND provider_message_id IS NULL "
            "AND error_code IS NOT NULL "
            "AND sent_at IS NULL"
            ")",
            name="ck_become_media_prompt_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_become_media_prompt_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "workspace_id"],
            ["agent_installations.id", "agent_installations.workspace_id"],
            name="fk_become_media_prompt_installation_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["consent_receipt_id"],
            ["become_controlled_consent_receipts.id"],
            name="fk_become_media_prompt_consent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relations_handoff_id"],
            ["become_relations_consent_handoffs.id"],
            name="fk_become_media_prompt_relations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id",
            name="uq_become_media_prompt_installation",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "prompt_key",
            name="uq_become_media_prompt_workspace_key",
        ),
    )
    op.create_index(
        "ix_become_media_prompt_workspace_state",
        "become_media_prompts",
        ["workspace_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_become_media_prompt_workspace_state",
        table_name="become_media_prompts",
    )
    op.drop_table("become_media_prompts")
