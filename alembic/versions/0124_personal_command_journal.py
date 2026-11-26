"""Add the personal Telegram RU command journal. All tables start empty."""

import sqlalchemy as sa
from alembic import op

revision = "0124_personal_command_journal"
down_revision = "0123_watch_mark_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_command_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("owner_subject", sa.String(length=256), nullable=False),
        sa.Column("session_generation", sa.Integer(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("custody_state", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.Uuid(), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_state", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "account_id = '0d8e025f-323d-5e86-a44c-a620f690ae4f'",
            name="ck_personal_command_ru_only",
        ),
        sa.CheckConstraint(
            "custody_state IN ('prepared','transition','revoked','synthetic')",
            name="ck_personal_command_custody_state",
        ),
        sa.CheckConstraint(
            "revision >= 0 AND session_generation > 0 AND lease_generation >= 0",
            name="ck_personal_command_generations",
        ),
        sa.CheckConstraint(
            "custody_state <> 'synthetic' OR (length(ciphertext) = 0 AND lease_owner IS NULL)",
            name="ck_personal_command_synthetic_binding",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace", "account_id", name="uq_personal_command_workspace_ru"),
    )
    op.create_table(
        "personal_command_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(length=256), nullable=False),
        sa.Column("client_namespace", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("approval_ref", sa.String(length=128), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_personal_grant_revision"),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["personal_command_bindings.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_personal_command_grants_binding_id"),
        "personal_command_grants",
        ["binding_id"],
        unique=False,
    )
    op.create_table(
        "personal_command_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(length=256), nullable=False),
        sa.Column("client_namespace", sa.String(length=128), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("peer_key", sa.String(length=64), nullable=False),
        sa.Column("payload_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("receipt_ref", sa.String(length=128), nullable=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('prepared','queued','running','succeeded',"
            "'rejected','cancelled','expired','unknown')",
            name="ck_personal_command_state",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["personal_command_bindings.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "binding_id",
            "owner_subject",
            "client_namespace",
            "request_key",
            name="uq_personal_command_intent",
        ),
    )
    op.create_index(
        op.f("ix_personal_command_operations_available_at"),
        "personal_command_operations",
        ["available_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_personal_command_operations_binding_id"),
        "personal_command_operations",
        ["binding_id"],
        unique=False,
    )
    op.create_index(
        "ix_personal_command_peer_state",
        "personal_command_operations",
        ["binding_id", "peer_key", "state"],
        unique=False,
    )
    op.create_index(
        "ix_personal_command_queue",
        "personal_command_operations",
        ["binding_id", "state", "available_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "personal_command_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("lease_owner", sa.Uuid(), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("random_id", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["personal_command_operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="uq_personal_command_one_attempt"),
    )


def downgrade() -> None:
    op.drop_table("personal_command_attempts")
    op.drop_table("personal_command_operations")
    op.drop_table("personal_command_grants")
    op.drop_table("personal_command_bindings")
