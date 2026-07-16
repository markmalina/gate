"""Scoped, non-mutating Alpha retention planning."""

from datetime import UTC, datetime

from app.application.alpha_retention import (
    AlphaDataClass,
    AlphaRetentionInventory,
    alpha_retention_payload,
    build_alpha_retention_dry_run,
)


NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _item(
    data_class: AlphaDataClass,
    count: int,
    *,
    installation_ref: str = "installation-alpha",
    created_at: datetime = datetime(2025, 1, 1, tzinfo=UTC),
) -> AlphaRetentionInventory:
    return AlphaRetentionInventory(
        workspace_key="mark-personal",
        installation_ref=installation_ref,
        data_class=data_class,
        record_count=count,
        oldest_created_at=created_at,
    )


def test_retention_plan_is_dry_run_and_never_authorizes_deletion() -> None:
    plan = build_alpha_retention_dry_run(
        tuple(_item(data_class, 2) for data_class in AlphaDataClass),
        workspace_key="mark-personal",
        installation_ref="installation-alpha",
        now=NOW,
    )

    assert plan.execution_mode == "dry_run"
    assert plan.mutation_allowed is False
    assert len(plan.items) == 5
    assert all(not item.automatic_deletion_allowed for item in plan.items)


def test_retention_plan_cannot_cross_installations() -> None:
    plan = build_alpha_retention_dry_run(
        (
            _item(AlphaDataClass.RAW_RECEIPT, 3),
            _item(
                AlphaDataClass.RAW_RECEIPT,
                99,
                installation_ref="installation-other",
            ),
        ),
        workspace_key="mark-personal",
        installation_ref="installation-alpha",
        now=NOW,
    )

    raw = next(
        item for item in plan.items if item.data_class is AlphaDataClass.RAW_RECEIPT
    )
    assert raw.observed_records == 3
    assert raw.potentially_expired_records == 3


def test_canonical_facts_have_no_automatic_ttl() -> None:
    plan = build_alpha_retention_dry_run(
        (_item(AlphaDataClass.CANONICAL_FACT, 4),),
        workspace_key="mark-personal",
        installation_ref="installation-alpha",
        now=NOW,
    )
    canonical = next(
        item
        for item in plan.items
        if item.data_class is AlphaDataClass.CANONICAL_FACT
    )

    assert canonical.cutoff_at is None
    assert canonical.potentially_expired_records == 0
    assert canonical.automatic_deletion_allowed is False


def test_retention_payload_contains_counts_not_content_or_provider_ids() -> None:
    plan = build_alpha_retention_dry_run(
        (_item(AlphaDataClass.MODEL_EVIDENCE, 1),),
        workspace_key="mark-personal",
        installation_ref="installation-alpha",
        now=NOW,
    )
    payload = alpha_retention_payload(plan)
    serialized = str(payload).casefold()

    assert payload["mutation_allowed"] is False
    assert "message_text" not in serialized
    assert "prompt" not in serialized
    assert "telegram_id" not in serialized
    assert "provider_id" not in serialized
