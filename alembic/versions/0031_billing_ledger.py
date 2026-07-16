"""Add provider-neutral billing ledger, entitlements and budget reservations.

Revision ID: 0031_billing_ledger
Revises: 0030_relationship_crm_commercial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_billing_ledger"
down_revision: str | None = "0030_relationship_crm_commercial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRODUCTS = "'gate', 'alpha', 'relationship_crm', 'mayaki', 'oko'"
_OPERATIONS = (
    "'model_response', 'transcription', 'telegram_delivery', "
    "'infrastructure', 'subscription'"
)


def _identity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _scope_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("product", sa.String(length=32), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_ref", sa.String(length=255), nullable=False),
    ]


def _workspace_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspaces.id"],
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "billing_entitlements",
        *_identity_columns(),
        sa.Column("entitlement_key", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("plan_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hard_limit_microusd", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"product IN ({_PRODUCTS})", name="ck_billing_entitlements_product"),
        sa.CheckConstraint("state IN ('active', 'paused', 'revoked', 'expired')", name="ck_billing_entitlements_state"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_billing_entitlements_window"),
        sa.CheckConstraint("hard_limit_microusd IS NULL OR hard_limit_microusd >= 0", name="ck_billing_entitlements_limit"),
        _workspace_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "entitlement_key", name="uq_billing_entitlements_workspace_key"),
    )
    op.create_index(
        "ix_billing_entitlements_scope",
        "billing_entitlements",
        ["workspace_id", "product", "scope_kind", "scope_ref"],
        unique=False,
    )

    op.create_table(
        "billing_usage_events",
        *_identity_columns(),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("provider_operation_ref", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"product IN ({_PRODUCTS})", name="ck_billing_usage_events_product"),
        sa.CheckConstraint(f"operation IN ({_OPERATIONS})", name="ck_billing_usage_events_operation"),
        sa.CheckConstraint("unit IN ('tokens', 'seconds', 'attempts', 'events', 'months')", name="ck_billing_usage_events_unit"),
        sa.CheckConstraint("quantity >= 0", name="ck_billing_usage_events_quantity"),
        sa.CheckConstraint("cost_microusd IS NULL OR cost_microusd >= 0", name="ck_billing_usage_events_cost"),
        _workspace_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "event_key", name="uq_billing_usage_events_workspace_key"),
        sa.UniqueConstraint(
            "workspace_id", "provider_operation_ref", "operation",
            name="uq_billing_usage_events_provider_operation",
        ),
    )
    op.create_index(
        "ix_billing_usage_events_scope_time",
        "billing_usage_events",
        ["workspace_id", "product", "scope_kind", "scope_ref", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "billing_ledger_entries",
        *_identity_columns(),
        sa.Column("entry_key", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("amount_microusd", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source_event_key", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"product IN ({_PRODUCTS})", name="ck_billing_ledger_entries_product"),
        sa.CheckConstraint("direction IN ('cost', 'revenue', 'credit', 'adjustment')", name="ck_billing_ledger_entries_direction"),
        sa.CheckConstraint("amount_microusd >= 0", name="ck_billing_ledger_entries_amount"),
        sa.CheckConstraint("currency = 'USD'", name="ck_billing_ledger_entries_currency"),
        _workspace_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "entry_key", name="uq_billing_ledger_entries_workspace_key"),
    )
    op.create_index(
        "ix_billing_ledger_entries_scope_time",
        "billing_ledger_entries",
        ["workspace_id", "product", "scope_kind", "scope_ref", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "billing_budget_reservations",
        *_identity_columns(),
        sa.Column("reservation_key", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("period_key", sa.String(length=64), nullable=False),
        sa.Column("reserved_microusd", sa.BigInteger(), nullable=False),
        sa.Column("settled_microusd", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"product IN ({_PRODUCTS})", name="ck_billing_budget_reservations_product"),
        sa.CheckConstraint(f"operation IN ({_OPERATIONS})", name="ck_billing_budget_reservations_operation"),
        sa.CheckConstraint("state IN ('reserved', 'settled', 'released', 'unknown')", name="ck_billing_budget_reservations_state"),
        sa.CheckConstraint("reserved_microusd > 0", name="ck_billing_budget_reservations_reserved"),
        sa.CheckConstraint(
            "settled_microusd IS NULL OR "
            "(settled_microusd >= 0 AND settled_microusd <= reserved_microusd)",
            name="ck_billing_budget_reservations_settled",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND settled_microusd IS NULL AND error_code IS NULL) OR "
            "(state = 'settled' AND settled_microusd IS NOT NULL AND error_code IS NULL) OR "
            "(state = 'released' AND settled_microusd IS NULL AND error_code IS NULL) OR "
            "(state = 'unknown' AND settled_microusd IS NULL AND error_code IS NOT NULL)",
            name="ck_billing_budget_reservations_payload",
        ),
        _workspace_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "reservation_key",
            name="uq_billing_budget_reservations_workspace_key",
        ),
    )
    op.create_index(
        "ix_billing_budget_reservations_period_state",
        "billing_budget_reservations",
        ["workspace_id", "product", "period_key", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_billing_budget_reservations_period_state", table_name="billing_budget_reservations")
    op.drop_table("billing_budget_reservations")
    op.drop_index("ix_billing_ledger_entries_scope_time", table_name="billing_ledger_entries")
    op.drop_table("billing_ledger_entries")
    op.drop_index("ix_billing_usage_events_scope_time", table_name="billing_usage_events")
    op.drop_table("billing_usage_events")
    op.drop_index("ix_billing_entitlements_scope", table_name="billing_entitlements")
    op.drop_table("billing_entitlements")
