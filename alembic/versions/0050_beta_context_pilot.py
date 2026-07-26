"""Add the durable Gate outbox and co-located Beta context sink.

Revision ID: 0050_beta_context_pilot
Revises: 0049_mayak_connector_active
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_beta_context_pilot"
down_revision: str | None = "0049_mayak_connector_active"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STROI_INSTALLATION_ID = "7fbf0214-5be5-5011-ae83-07e57dd8216c"
_STROI_INSTALLATION_REF = "installation-7fbf02145be55011ae8307e57dd8216c"
_STROI_GROUP_ID = "beta-group-stroi-alphav-001"
_USER_WORKSPACE_ID = "beta-user-mark-001"


def upgrade() -> None:
    op.create_table(
        "beta_context_bindings",
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_ref", sa.String(length=96), nullable=False),
        sa.Column("user_workspace_id", sa.String(length=128), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
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
            "state IN ('active', 'paused', 'revoked')",
            name="ck_beta_context_binding_state",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["agent_installations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("installation_id"),
        sa.UniqueConstraint("group_id", name="uq_beta_context_binding_group"),
        sa.UniqueConstraint(
            "installation_ref", name="uq_beta_context_binding_ref"
        ),
    )
    op.create_table(
        "gate_context_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_event_ref", sa.String(length=96), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_ref", sa.String(length=80), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("canonical_sequence", sa.BigInteger(), nullable=True),
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
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_gate_context_outbox_direction",
        ),
        sa.CheckConstraint(
            "kind IN ('message.inbound', 'message.outbound', 'fact', "
            "'decision', 'correction', 'tombstone', 'boundary')",
            name="ck_gate_context_outbox_kind",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'dead_letter')",
            name="ck_gate_context_outbox_state",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["beta_context_bindings.installation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id",
            "gate_event_ref",
            name="uq_gate_context_outbox_event_ref",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_gate_context_outbox_idempotency",
        ),
    )
    op.create_index(
        "ix_gate_context_outbox_pending",
        "gate_context_outbox",
        ["state", "created_at"],
        unique=False,
    )
    op.create_table(
        "beta_context_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=96), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor_ref", sa.String(length=80), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_event_ref", sa.String(length=96), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["beta_context_bindings.installation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint(
            "group_id", "sequence", name="uq_beta_context_group_sequence"
        ),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_beta_context_idempotency",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "source_event_ref",
            name="uq_beta_context_source_event",
        ),
    )
    op.create_index(
        "ix_beta_context_group_sequence",
        "beta_context_events",
        ["group_id", "sequence"],
        unique=False,
    )
    op.create_table(
        "beta_context_checkpoint_state",
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column(
            "last_sequence", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "last_exported_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["beta_context_bindings.installation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("installation_id"),
        sa.UniqueConstraint("group_id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO beta_context_bindings "
            "(installation_id, installation_ref, user_workspace_id, group_id, state) "
            "SELECT id, :installation_ref, :user_workspace_id, :group_id, 'active' "
            "FROM agent_installations WHERE id = :installation_id "
            "ON CONFLICT (installation_id) DO NOTHING"
        ).bindparams(
            installation_id=_STROI_INSTALLATION_ID,
            installation_ref=_STROI_INSTALLATION_REF,
            user_workspace_id=_USER_WORKSPACE_ID,
            group_id=_STROI_GROUP_ID,
        )
    )


def downgrade() -> None:
    op.drop_table("beta_context_checkpoint_state")
    op.drop_index(
        "ix_beta_context_group_sequence", table_name="beta_context_events"
    )
    op.drop_table("beta_context_events")
    op.drop_index(
        "ix_gate_context_outbox_pending", table_name="gate_context_outbox"
    )
    op.drop_table("gate_context_outbox")
    op.drop_table("beta_context_bindings")
