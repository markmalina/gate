"""Dry-run privacy and retention planning for Alpha data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class AlphaDataClass(StrEnum):
    RAW_RECEIPT = "raw_receipt"
    BOUNDED_CONTEXT = "bounded_context"
    MODEL_EVIDENCE = "model_evidence"
    DELIVERY_AUDIT = "delivery_audit"
    CANONICAL_FACT = "canonical_fact"


@dataclass(frozen=True, slots=True)
class AlphaRetentionPolicy:
    data_class: AlphaDataClass
    ttl_days: int | None
    automatic_deletion_allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AlphaRetentionInventory:
    workspace_key: str
    installation_ref: str
    data_class: AlphaDataClass
    record_count: int
    oldest_created_at: datetime | None

    def __post_init__(self) -> None:
        if not self.workspace_key or not self.installation_ref:
            raise ValueError("alpha_retention_scope_required")
        if self.record_count < 0:
            raise ValueError("alpha_retention_count_invalid")


@dataclass(frozen=True, slots=True)
class AlphaRetentionPlanItem:
    data_class: AlphaDataClass
    observed_records: int
    potentially_expired_records: int
    cutoff_at: datetime | None
    automatic_deletion_allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AlphaRetentionDryRun:
    workspace_key: str
    installation_ref: str
    evaluated_at: datetime
    execution_mode: str
    mutation_allowed: bool
    items: tuple[AlphaRetentionPlanItem, ...]


ALPHA_RETENTION_POLICIES = (
    AlphaRetentionPolicy(
        data_class=AlphaDataClass.RAW_RECEIPT,
        ttl_days=30,
        automatic_deletion_allowed=False,
        reason="owner_review_required_before_raw_receipt_deletion",
    ),
    AlphaRetentionPolicy(
        data_class=AlphaDataClass.BOUNDED_CONTEXT,
        ttl_days=7,
        automatic_deletion_allowed=False,
        reason="derived_context_must_not_outlive_approved_scope",
    ),
    AlphaRetentionPolicy(
        data_class=AlphaDataClass.MODEL_EVIDENCE,
        ttl_days=30,
        automatic_deletion_allowed=False,
        reason="content_free_model_evidence_requires_reviewed_deletion",
    ),
    AlphaRetentionPolicy(
        data_class=AlphaDataClass.DELIVERY_AUDIT,
        ttl_days=365,
        automatic_deletion_allowed=False,
        reason="delivery_safety_audit_requires_owner_review",
    ),
    AlphaRetentionPolicy(
        data_class=AlphaDataClass.CANONICAL_FACT,
        ttl_days=None,
        automatic_deletion_allowed=False,
        reason="canonical_facts_follow_explicit_owner_or_subject_deletion",
    ),
)


def build_alpha_retention_dry_run(
    inventory: tuple[AlphaRetentionInventory, ...],
    *,
    workspace_key: str,
    installation_ref: str,
    now: datetime,
) -> AlphaRetentionDryRun:
    """Plan bounded retention without deleting or returning record identifiers."""

    policies = {policy.data_class: policy for policy in ALPHA_RETENTION_POLICIES}
    if set(policies) != set(AlphaDataClass):
        raise ValueError("alpha_retention_policy_coverage_invalid")
    if not workspace_key or not installation_ref:
        raise ValueError("alpha_retention_scope_required")
    scoped = tuple(
        item
        for item in inventory
        if item.workspace_key == workspace_key
        and item.installation_ref == installation_ref
    )

    plan_items: list[AlphaRetentionPlanItem] = []
    for data_class in AlphaDataClass:
        policy = policies[data_class]
        matching = tuple(
            item for item in scoped if item.data_class is data_class
        )
        observed = sum(item.record_count for item in matching)
        cutoff = (
            now - timedelta(days=policy.ttl_days)
            if policy.ttl_days is not None
            else None
        )
        potentially_expired = sum(
            item.record_count
            for item in matching
            if cutoff is not None
            and item.oldest_created_at is not None
            and item.oldest_created_at < cutoff
        )
        plan_items.append(
            AlphaRetentionPlanItem(
                data_class=data_class,
                observed_records=observed,
                potentially_expired_records=potentially_expired,
                cutoff_at=cutoff,
                automatic_deletion_allowed=policy.automatic_deletion_allowed,
                reason=policy.reason,
            )
        )

    return AlphaRetentionDryRun(
        workspace_key=workspace_key,
        installation_ref=installation_ref,
        evaluated_at=now,
        execution_mode="dry_run",
        mutation_allowed=False,
        items=tuple(plan_items),
    )


def alpha_retention_payload(plan: AlphaRetentionDryRun) -> dict[str, object]:
    """Serialize only counts, cutoffs and opaque scope references."""

    return {
        "workspace_key": plan.workspace_key,
        "installation_ref": plan.installation_ref,
        "evaluated_at": plan.evaluated_at.isoformat(),
        "execution_mode": plan.execution_mode,
        "mutation_allowed": plan.mutation_allowed,
        "items": [
            {
                "data_class": item.data_class.value,
                "observed_records": item.observed_records,
                "potentially_expired_records": item.potentially_expired_records,
                "cutoff_at": (
                    item.cutoff_at.isoformat()
                    if item.cutoff_at is not None
                    else None
                ),
                "automatic_deletion_allowed": item.automatic_deletion_allowed,
                "reason": item.reason,
            }
            for item in plan.items
        ],
    }
