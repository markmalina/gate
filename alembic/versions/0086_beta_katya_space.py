"""Add isolated Beta Space binding, event and consent-delivery ledgers.

Revision ID: 0086_beta_katya_space
Revises: 0085_become_pilot_slice2_program
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0086_beta_katya_space"
down_revision: str | None = "0085_become_pilot_slice2_program"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beta_space_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_ref", sa.String(length=128), nullable=False),
        sa.Column("space_ref", sa.String(length=128), nullable=False),
        sa.Column("installation_ref", sa.String(length=128), nullable=False),
        sa.Column("storage_ref", sa.String(length=128), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=255), nullable=False),
        sa.Column("subject_telegram_user_id", sa.String(length=255), nullable=False),
        sa.Column("consent_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="ready", nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('ready','consented','revoked')", name="ck_beta_space_binding_state"
        ),
        sa.CheckConstraint(
            "char_length(consent_sha256) = 64", name="ck_beta_space_binding_consent_sha"
        ),
        sa.CheckConstraint(
            "(state = 'consented' AND consented_at IS NOT NULL) OR "
            "(state != 'consented' AND consented_at IS NULL)",
            name="ck_beta_space_binding_consented_at",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id", "workspace_id"],
            ["transport_connectors.id", "transport_connectors.workspace_id"],
            name="fk_beta_space_binding_connector_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_beta_space_binding_id_workspace"),
        sa.UniqueConstraint(
            "workspace_id", "tenant_ref", "space_ref", name="uq_beta_space_binding_space"
        ),
        sa.UniqueConstraint(
            "connector_id", "telegram_chat_id", name="uq_beta_space_binding_chat"
        ),
        sa.UniqueConstraint("installation_ref", name="uq_beta_space_binding_installation"),
        sa.UniqueConstraint("storage_ref", name="uq_beta_space_binding_storage"),
    )

    op.create_table(
        "beta_space_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=255), nullable=False),
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("media_kind", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("event_kind IN ('consent','fact')", name="ck_beta_space_event_kind"),
        sa.CheckConstraint(
            "media_kind IN ('text','voice','photo','document')",
            name="ck_beta_space_event_media_kind",
        ),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64", name="ck_beta_space_event_payload_sha"
        ),
        sa.ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_space_event_binding_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["receipt_id"], ["telegram_receipts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_id", name="uq_beta_space_event_receipt"),
        sa.UniqueConstraint("binding_id", "update_id", name="uq_beta_space_event_update"),
        sa.UniqueConstraint(
            "binding_id", "telegram_message_id", name="uq_beta_space_event_message"
        ),
    )
    op.create_index(
        "ix_beta_space_event_scope_occurred",
        "beta_space_events",
        ["workspace_id", "binding_id", "occurred_at"],
    )

    op.create_table(
        "beta_consent_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending','reserved','sent','failed','unknown')",
            name="ck_beta_consent_delivery_state",
        ),
        sa.CheckConstraint(
            "char_length(request_sha256) = 64", name="ck_beta_consent_delivery_request_sha"
        ),
        sa.CheckConstraint(
            "(state IN ('pending','reserved') AND provider_message_id IS NULL "
            "AND settled_at IS NULL) OR "
            "(state = 'sent' AND provider_message_id IS NOT NULL "
            "AND settled_at IS NOT NULL) OR "
            "(state IN ('failed','unknown') AND provider_message_id IS NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_beta_consent_delivery_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id", "workspace_id"],
            ["beta_space_bindings.id", "beta_space_bindings.workspace_id"],
            name="fk_beta_consent_delivery_binding_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["beta_space_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_beta_consent_delivery_event"),
        sa.UniqueConstraint("request_sha256", name="uq_beta_consent_delivery_request"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    event_count = connection.execute(sa.text("SELECT count(*) FROM beta_space_events")).scalar_one()
    delivery_count = connection.execute(
        sa.text("SELECT count(*) FROM beta_consent_deliveries")
    ).scalar_one()
    if event_count or delivery_count:
        raise RuntimeError("beta_space_live_data_blocks_downgrade")
    op.drop_table("beta_consent_deliveries")
    op.drop_index("ix_beta_space_event_scope_occurred", table_name="beta_space_events")
    op.drop_table("beta_space_events")
    op.drop_table("beta_space_bindings")
