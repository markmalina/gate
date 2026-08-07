"""Add encrypted checkpoint durability for the owner Wedding Space.

Revision ID: 0090_beta_wedding_checkpoints
Revises: 0089_beta_wedding_voice_phases
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0090_beta_wedding_checkpoints"
down_revision: str | None = "0089_beta_wedding_voice_phases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WEDDING_SPACE_REF = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"
_WEDDING_INSTALLATION_REF = "installation-4de1df8a77f8468d8b30e8a41db876aa"
_RECIPIENT_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VuAyEAJOsuz1/C1RbIFfazec4EdZlXFFWln8UdWPJ1jddC3Fo=
-----END PUBLIC KEY-----
"""


def upgrade() -> None:
    op.add_column(
        "beta_space_bindings",
        sa.Column("checkpoint_recipient_pem", sa.Text(), nullable=True),
    )
    op.add_column(
        "beta_space_bindings",
        sa.Column(
            "checkpoint_last_exported_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "beta_space_events",
        sa.Column("checkpoint_sequence", sa.BigInteger(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            WITH ordered AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY binding_id
                           ORDER BY created_at, id
                       ) AS sequence
                FROM beta_space_events
            )
            UPDATE beta_space_events AS event
            SET checkpoint_sequence = ordered.sequence
            FROM ordered
            WHERE event.id = ordered.id
            """
        )
    )
    op.alter_column(
        "beta_space_events",
        "checkpoint_sequence",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_beta_space_event_checkpoint_sequence",
        "beta_space_events",
        ["binding_id", "checkpoint_sequence"],
    )
    op.execute(
        sa.text(
            """
            UPDATE beta_space_bindings
            SET checkpoint_recipient_pem = :recipient
            WHERE space_ref = :space_ref
              AND installation_ref = :installation_ref
              AND state = 'consented'
            """
        ).bindparams(
            recipient=_RECIPIENT_PEM,
            space_ref=_WEDDING_SPACE_REF,
            installation_ref=_WEDDING_INSTALLATION_REF,
        )
    )
    op.create_table(
        "beta_space_checkpoint_exports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("beta_space_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("sequence_start", sa.BigInteger(), nullable=False),
        sa.Column("sequence_end", sa.BigInteger(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("container", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(64), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("github_commit_sha", sa.String(40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "binding_id",
            "sequence_start",
            name="uq_beta_space_checkpoint_export_start",
        ),
        sa.CheckConstraint(
            "sequence_end >= sequence_start",
            name="ck_beta_space_checkpoint_export_range",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'acknowledged')",
            name="ck_beta_space_checkpoint_export_state",
        ),
    )
    op.create_index(
        "ix_beta_space_checkpoint_export_pending",
        "beta_space_checkpoint_exports",
        ["binding_id", "state", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_beta_space_checkpoint_export_pending",
        table_name="beta_space_checkpoint_exports",
    )
    op.drop_table("beta_space_checkpoint_exports")
    op.drop_constraint(
        "uq_beta_space_event_checkpoint_sequence",
        "beta_space_events",
        type_="unique",
    )
    op.drop_column("beta_space_events", "checkpoint_sequence")
    op.drop_column(
        "beta_space_bindings",
        "checkpoint_last_exported_sequence",
    )
    op.drop_column("beta_space_bindings", "checkpoint_recipient_pem")
