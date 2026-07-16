"""Provider-neutral billing, entitlement and budget persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.domain import Base

_PRODUCTS = "'gate', 'alpha', 'relationship_crm', 'mayaki', 'oko'"


class BillingEntitlementModel(Base):
    __tablename__ = "billing_entitlements"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "entitlement_key",
            name="uq_billing_entitlements_workspace_key",
        ),
        CheckConstraint(
            f"product IN ({_PRODUCTS})",
            name="ck_billing_entitlements_product",
        ),
        CheckConstraint(
            "state IN ('active', 'paused', 'revoked', 'expired')",
            name="ck_billing_entitlements_state",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_billing_entitlements_window",
        ),
        CheckConstraint(
            "hard_limit_microusd IS NULL OR hard_limit_microusd >= 0",
            name="ck_billing_entitlements_limit",
        ),
        Index(
            "ix_billing_entitlements_scope",
            "workspace_id",
            "product",
            "scope_kind",
            "scope_ref",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    entitlement_key: Mapped[str] = mapped_column(String(128), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hard_limit_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingUsageEventModel(Base):
    __tablename__ = "billing_usage_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "event_key",
            name="uq_billing_usage_events_workspace_key",
        ),
        UniqueConstraint(
            "workspace_id",
            "provider_operation_ref",
            "operation",
            name="uq_billing_usage_events_provider_operation",
        ),
        CheckConstraint(
            f"product IN ({_PRODUCTS})",
            name="ck_billing_usage_events_product",
        ),
        CheckConstraint(
            "operation IN ('model_response', 'transcription', "
            "'telegram_delivery', 'infrastructure', 'subscription')",
            name="ck_billing_usage_events_operation",
        ),
        CheckConstraint(
            "unit IN ('tokens', 'seconds', 'attempts', 'events', 'months')",
            name="ck_billing_usage_events_unit",
        ),
        CheckConstraint("quantity >= 0", name="ck_billing_usage_events_quantity"),
        CheckConstraint(
            "cost_microusd IS NULL OR cost_microusd >= 0",
            name="ck_billing_usage_events_cost",
        ),
        Index(
            "ix_billing_usage_events_scope_time",
            "workspace_id",
            "product",
            "scope_kind",
            "scope_ref",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_operation_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingLedgerEntryModel(Base):
    __tablename__ = "billing_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "entry_key",
            name="uq_billing_ledger_entries_workspace_key",
        ),
        CheckConstraint(
            f"product IN ({_PRODUCTS})",
            name="ck_billing_ledger_entries_product",
        ),
        CheckConstraint(
            "direction IN ('cost', 'revenue', 'credit', 'adjustment')",
            name="ck_billing_ledger_entries_direction",
        ),
        CheckConstraint(
            "amount_microusd >= 0",
            name="ck_billing_ledger_entries_amount",
        ),
        CheckConstraint(
            "currency = 'USD'",
            name="ck_billing_ledger_entries_currency",
        ),
        Index(
            "ix_billing_ledger_entries_scope_time",
            "workspace_id",
            "product",
            "scope_kind",
            "scope_ref",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    entry_key: Mapped[str] = mapped_column(String(128), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_event_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingBudgetReservationModel(Base):
    __tablename__ = "billing_budget_reservations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "reservation_key",
            name="uq_billing_budget_reservations_workspace_key",
        ),
        CheckConstraint(
            f"product IN ({_PRODUCTS})",
            name="ck_billing_budget_reservations_product",
        ),
        CheckConstraint(
            "operation IN ('model_response', 'transcription', "
            "'telegram_delivery', 'infrastructure', 'subscription')",
            name="ck_billing_budget_reservations_operation",
        ),
        CheckConstraint(
            "state IN ('reserved', 'settled', 'released', 'unknown')",
            name="ck_billing_budget_reservations_state",
        ),
        CheckConstraint(
            "reserved_microusd > 0",
            name="ck_billing_budget_reservations_reserved",
        ),
        CheckConstraint(
            "settled_microusd IS NULL OR "
            "(settled_microusd >= 0 AND settled_microusd <= reserved_microusd)",
            name="ck_billing_budget_reservations_settled",
        ),
        CheckConstraint(
            "(state = 'reserved' AND settled_microusd IS NULL "
            "AND error_code IS NULL) OR "
            "(state = 'settled' AND settled_microusd IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(state = 'released' AND settled_microusd IS NULL "
            "AND error_code IS NULL) OR "
            "(state = 'unknown' AND settled_microusd IS NULL "
            "AND error_code IS NOT NULL)",
            name="ck_billing_budget_reservations_payload",
        ),
        Index(
            "ix_billing_budget_reservations_period_state",
            "workspace_id",
            "product",
            "period_key",
            "state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    reservation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
