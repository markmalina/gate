"""Schema guards for durable provider-neutral billing persistence."""

import importlib.util
from pathlib import Path

from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
    BillingLedgerEntryModel,
    BillingUsageEventModel,
)
from sqlalchemy import CheckConstraint, UniqueConstraint

_MODELS = (
    BillingEntitlementModel,
    BillingUsageEventModel,
    BillingLedgerEntryModel,
    BillingBudgetReservationModel,
)


def _unique_column_sets(model: type[object]) -> set[tuple[str, ...]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_every_billing_record_is_workspace_and_product_scoped() -> None:
    for model in _MODELS:
        columns = set(model.__table__.columns.keys())  # type: ignore[attr-defined]
        assert {"workspace_id", "product", "scope_kind", "scope_ref"}.issubset(columns)


def test_external_side_effects_are_idempotently_attributed() -> None:
    assert ("workspace_id", "event_key") in _unique_column_sets(
        BillingUsageEventModel
    )
    assert (
        "workspace_id",
        "provider_operation_ref",
        "operation",
    ) in _unique_column_sets(BillingUsageEventModel)
    assert ("workspace_id", "entry_key") in _unique_column_sets(
        BillingLedgerEntryModel
    )
    assert ("workspace_id", "reservation_key") in _unique_column_sets(
        BillingBudgetReservationModel
    )


def test_taxonomies_and_budget_state_are_database_constrained() -> None:
    assert "ck_billing_entitlements_product" in _check_names(
        BillingEntitlementModel
    )
    assert "ck_billing_usage_events_operation" in _check_names(
        BillingUsageEventModel
    )
    assert "ck_billing_ledger_entries_direction" in _check_names(
        BillingLedgerEntryModel
    )
    assert {
        "ck_billing_budget_reservations_state",
        "ck_billing_budget_reservations_payload",
        "ck_billing_budget_reservations_settled",
    }.issubset(_check_names(BillingBudgetReservationModel))


def test_billing_storage_excludes_secrets_and_message_content() -> None:
    forbidden = {
        "api_key",
        "secret",
        "access_token",
        "refresh_token",
        "telegram_chat_id",
        "message_text",
        "transcript",
    }
    for model in _MODELS:
        columns = set(model.__table__.columns.keys())  # type: ignore[attr-defined]
        assert not columns.intersection(forbidden)


def test_billing_migration_follows_current_head_and_is_reversible() -> None:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0031_billing_ledger.py"
    spec = importlib.util.spec_from_file_location("billing_ledger_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0031_billing_ledger"
    assert module.down_revision == "0030_relationship_crm_commercial"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_alembic_metadata_registers_billing_tables() -> None:
    env_source = (Path(__file__).parents[1] / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    assert "billing" in env_source
