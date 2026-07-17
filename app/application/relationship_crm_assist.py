"""Owner-confirmed Relationship CRM suggestions with explicit provenance."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from app.application.relationship_crm import RelationshipCrmDomainError


class RelationshipSuggestionKind(StrEnum):
    SUMMARY = "summary"
    ATTENTION = "attention"
    FOLLOW_UP = "follow_up"
    DEAL_STAGE = "deal_stage"


class RelationshipSuggestionState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RelationshipSuggestionEvidence:
    source_kind: str
    source_ref: str = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        _token(self.source_kind, "suggestion_source_kind", 64)
        _token(self.source_ref, "suggestion_source_ref", 255)
        _aware(self.observed_at, "suggestion_evidence_time")


@dataclass(frozen=True, slots=True)
class RelationshipCrmSuggestion:
    suggestion_key: str
    workspace_key: str
    space_key: str
    relationship_key: str
    kind: RelationshipSuggestionKind
    proposed_value: str = field(repr=False)
    confidence_basis_points: int
    evidence: tuple[RelationshipSuggestionEvidence, ...] = field(repr=False)
    model_revision: str
    state: RelationshipSuggestionState
    revision: int
    created_at: datetime
    updated_at: datetime
    last_decision_key: str | None = None
    decided_by: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.suggestion_key, "suggestion_key"),
            (self.workspace_key, "suggestion_workspace_key"),
            (self.space_key, "suggestion_space_key"),
            (self.relationship_key, "suggestion_relationship_key"),
            (self.model_revision, "suggestion_model_revision"),
        ):
            _token(value, name, 128)
        if not self.proposed_value.strip() or len(self.proposed_value) > 16_384:
            raise RelationshipCrmDomainError("suggestion_value_invalid")
        if not 0 <= self.confidence_basis_points <= 10_000:
            raise RelationshipCrmDomainError("suggestion_confidence_invalid")
        if not self.evidence or len(self.evidence) > 50:
            raise RelationshipCrmDomainError("suggestion_evidence_invalid")
        if self.revision < 1:
            raise RelationshipCrmDomainError("suggestion_revision_invalid")
        _aware(self.created_at, "suggestion_created_at")
        _aware(self.updated_at, "suggestion_updated_at")
        if self.updated_at < self.created_at:
            raise RelationshipCrmDomainError("suggestion_time_regression")
        decided = self.state is not RelationshipSuggestionState.PENDING
        if decided != (
            self.last_decision_key is not None and self.decided_by is not None
        ):
            raise RelationshipCrmDomainError("suggestion_decision_payload_invalid")


@dataclass(frozen=True, slots=True)
class DecideRelationshipSuggestionCommand:
    decision_key: str
    workspace_key: str
    space_key: str
    suggestion_key: str
    expected_revision: int
    accepted: bool
    actor_key: str
    confirmed: bool
    occurred_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.decision_key, "suggestion_decision_key"),
            (self.workspace_key, "suggestion_workspace_key"),
            (self.space_key, "suggestion_space_key"),
            (self.suggestion_key, "suggestion_key"),
            (self.actor_key, "suggestion_actor_key"),
        ):
            _token(value, name, 128)
        if self.expected_revision < 1:
            raise RelationshipCrmDomainError("suggestion_expected_revision_invalid")
        if self.confirmed is not True:
            raise RelationshipCrmDomainError("suggestion_confirmation_required")
        _aware(self.occurred_at, "suggestion_decision_time")


@dataclass(frozen=True, slots=True)
class RelationshipSuggestionDecision:
    suggestion: RelationshipCrmSuggestion
    mutation_allowed: bool = False
    telegram_delivery_allowed: bool = False
    requires_separate_owner_command: bool = True

    def __post_init__(self) -> None:
        if self.mutation_allowed or self.telegram_delivery_allowed:
            raise RelationshipCrmDomainError("suggestion_decision_side_effect_forbidden")
        if self.requires_separate_owner_command is not True:
            raise RelationshipCrmDomainError("suggestion_owner_command_required")


def decide_relationship_suggestion(
    current: RelationshipCrmSuggestion,
    command: DecideRelationshipSuggestionCommand,
) -> RelationshipSuggestionDecision:
    """Record owner intent; never apply the proposed CRM mutation."""

    if (
        current.workspace_key,
        current.space_key,
        current.suggestion_key,
    ) != (
        command.workspace_key,
        command.space_key,
        command.suggestion_key,
    ):
        raise RelationshipCrmDomainError("suggestion_scope_mismatch")
    if current.last_decision_key == command.decision_key:
        expected_state = (
            RelationshipSuggestionState.ACCEPTED
            if command.accepted
            else RelationshipSuggestionState.REJECTED
        )
        if (
            current.state is not expected_state
            or current.revision != command.expected_revision + 1
            or current.decided_by != command.actor_key
            or current.updated_at != command.occurred_at
        ):
            raise RelationshipCrmDomainError("suggestion_decision_key_reused")
        return RelationshipSuggestionDecision(current)
    if current.state is not RelationshipSuggestionState.PENDING:
        raise RelationshipCrmDomainError("suggestion_already_decided")
    if current.revision != command.expected_revision:
        raise RelationshipCrmDomainError("suggestion_revision_conflict")
    if command.occurred_at < current.updated_at:
        raise RelationshipCrmDomainError("suggestion_decision_time_regression")

    updated = replace(
        current,
        state=(
            RelationshipSuggestionState.ACCEPTED
            if command.accepted
            else RelationshipSuggestionState.REJECTED
        ),
        revision=current.revision + 1,
        updated_at=command.occurred_at,
        last_decision_key=command.decision_key,
        decided_by=command.actor_key,
    )
    return RelationshipSuggestionDecision(updated)


def _token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RelationshipCrmDomainError(f"{field_name}_timezone_required")
