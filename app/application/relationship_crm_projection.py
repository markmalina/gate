"""Sanitized Gate-to-Relationship-CRM projection and write-free backfill planning."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from app.application.relationship_crm import (
    ConversationAttention,
    ConversationAttentionState,
    InteractionDirection,
    PersonRecord,
    RelationshipCrmDomainError,
    RelationshipInteraction,
    RelationshipInteractionKind,
    RelationshipRecord,
    RelationshipRole,
    RelationshipSpace,
    RelationshipSpaceKind,
)

PERSONAL_SPACE_KEY = "personal"
PROJECTION_ACTOR_KEY = "gate-projection"


@dataclass(frozen=True, slots=True)
class GateConversationProjectionFact:
    """Minimum sanitized transport fact. Provider identifiers and message text are excluded."""

    workspace_key: str
    person_key: str
    display_name: str = field(repr=False)
    conversation_ref: str = field(repr=False)
    interaction_ref: str = field(repr=False)
    direction: InteractionDirection
    occurred_at: datetime

    def __post_init__(self) -> None:
        _token(self.workspace_key, "workspace_key")
        _token(self.person_key, "person_key")
        _text(self.display_name, "display_name", 255)
        _token(self.conversation_ref, "conversation_ref", 255)
        _token(self.interaction_ref, "interaction_ref", 255)
        _aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class PersonalProjectionPlan:
    space: RelationshipSpace
    person: PersonRecord = field(repr=False)
    relationship: RelationshipRecord
    interaction: RelationshipInteraction = field(repr=False)
    attention: ConversationAttention = field(repr=False)


@dataclass(frozen=True, slots=True)
class RelationshipCrmBackfillDryRun:
    input_count: int
    planned_count: int
    duplicate_count: int
    conflict_count: int
    workspace_count: int
    projection_sha256: str
    attention_state: ConversationAttentionState = ConversationAttentionState.UNTRIAGED
    writes_performed: bool = False

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.input_count,
                self.planned_count,
                self.duplicate_count,
                self.conflict_count,
                self.workspace_count,
            )
        ):
            raise RelationshipCrmDomainError("relationship_backfill_count_invalid")
        if len(self.projection_sha256) != 64:
            raise RelationshipCrmDomainError("relationship_backfill_digest_invalid")
        if self.writes_performed:
            raise RelationshipCrmDomainError("relationship_backfill_dry_run_cannot_write")


def project_gate_conversation(
    fact: GateConversationProjectionFact,
) -> PersonalProjectionPlan:
    """Project one fact without inferring attention from message direction."""

    relationship_key = _stable_key("personal-relationship", fact.person_key)
    interaction_key = _stable_key("gate-interaction", fact.interaction_ref)
    command_key = _stable_key("gate-attention", fact.conversation_ref)
    return PersonalProjectionPlan(
        space=RelationshipSpace(
            workspace_key=fact.workspace_key,
            space_key=PERSONAL_SPACE_KEY,
            kind=RelationshipSpaceKind.PERSONAL,
            name="Personal",
            created_at=fact.occurred_at,
        ),
        person=PersonRecord(
            workspace_key=fact.workspace_key,
            person_key=fact.person_key,
            display_name=fact.display_name,
            created_at=fact.occurred_at,
        ),
        relationship=RelationshipRecord(
            workspace_key=fact.workspace_key,
            space_key=PERSONAL_SPACE_KEY,
            space_kind=RelationshipSpaceKind.PERSONAL,
            relationship_key=relationship_key,
            person_key=fact.person_key,
            role=RelationshipRole.PERSONAL_CONTACT,
            created_at=fact.occurred_at,
        ),
        interaction=RelationshipInteraction(
            workspace_key=fact.workspace_key,
            space_key=PERSONAL_SPACE_KEY,
            relationship_key=relationship_key,
            person_key=fact.person_key,
            interaction_key=interaction_key,
            kind=RelationshipInteractionKind.MESSAGE,
            direction=fact.direction,
            occurred_at=fact.occurred_at,
            summary=None,
            source_ref=interaction_key,
        ),
        attention=ConversationAttention(
            workspace_key=fact.workspace_key,
            person_key=fact.person_key,
            conversation_key=_stable_key("gate-conversation", fact.conversation_ref),
            state=ConversationAttentionState.UNTRIAGED,
            revision=1,
            actor_key=PROJECTION_ACTOR_KEY,
            last_command_key=command_key,
            updated_at=fact.occurred_at,
        ),
    )


def dry_run_gate_backfill(
    facts: tuple[GateConversationProjectionFact, ...],
) -> RelationshipCrmBackfillDryRun:
    """Plan deterministic projections and return only content-free aggregate evidence."""

    by_identity: dict[
        tuple[str, str],
        GateConversationProjectionFact,
    ] = {}
    duplicates = 0
    conflicts = 0
    for fact in facts:
        identity = (fact.workspace_key, fact.interaction_ref)
        previous = by_identity.get(identity)
        if previous is None:
            by_identity[identity] = fact
        elif previous == fact:
            duplicates += 1
        else:
            conflicts += 1

    plans = tuple(
        project_gate_conversation(fact)
        for _, fact in sorted(by_identity.items(), key=lambda item: item[0])
    )
    material = "\n".join(
        (
            plan.space.workspace_key,
            plan.relationship.relationship_key,
            plan.interaction.interaction_key,
            plan.attention.state.value,
        )
        for plan in plans
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return RelationshipCrmBackfillDryRun(
        input_count=len(facts),
        planned_count=len(plans),
        duplicate_count=duplicates,
        conflict_count=conflicts,
        workspace_count=len({plan.space.workspace_key for plan in plans}),
        projection_sha256=digest,
    )


def _stable_key(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\n{value}".encode("utf-8")).hexdigest()
    return f"{namespace}:{digest[:32]}"


def _token(value: str, field_name: str, maximum: int = 128) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")


def _text(value: str, field_name: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RelationshipCrmDomainError(f"{field_name}_timezone_required")
