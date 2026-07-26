"""Persist deterministic Beta checkpoint batches and recipient bindings.

Revision ID: 0058_beta_checkpoint_exports
Revises: 0057_dropbox_call_claim
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_beta_checkpoint_exports"
down_revision: str | None = "0057_dropbox_call_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INSTALLATION_REF = "installation-7fbf02145be55011ae8307e57dd8216c"
_RECIPIENT_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VuAyEAJOsuz1/C1RbIFfazec4EdZlXFFWln8UdWPJ1jddC3Fo=
-----END PUBLIC KEY-----
"""


def upgrade() -> None:
    op.add_column(
        "beta_context_bindings",
        sa.Column("checkpoint_recipient_pem", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE beta_context_bindings "
            "SET checkpoint_recipient_pem = :recipient "
            "WHERE installation_ref = :installation_ref "
            "AND state = 'active'"
        ).bindparams(
            recipient=_RECIPIENT_PEM,
            installation_ref=_INSTALLATION_REF,
        )
    )
    op.create_table(
        "beta_context_checkpoint_exports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "beta_context_bindings.installation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("sequence_start", sa.BigInteger(), nullable=False),
        sa.Column("sequence_end", sa.BigInteger(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("container", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("github_commit_sha", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "installation_id",
            "sequence_start",
            name="uq_beta_checkpoint_export_start",
        ),
        sa.CheckConstraint(
            "sequence_end >= sequence_start",
            name="ck_beta_checkpoint_export_range",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'acknowledged')",
            name="ck_beta_checkpoint_export_state",
        ),
    )
    op.create_index(
        "ix_beta_checkpoint_export_pending",
        "beta_context_checkpoint_exports",
        ["installation_id", "state", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_beta_checkpoint_export_pending",
        table_name="beta_context_checkpoint_exports",
    )
    op.drop_table("beta_context_checkpoint_exports")
    op.drop_column(
        "beta_context_bindings",
        "checkpoint_recipient_pem",
    )
