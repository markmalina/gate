"""Pure domain contracts for the standalone Relationship CRM product.

The existing personal CRM contracts remain import-compatible.  This module adds the
space, relationship, commercial pipeline and deal vocabulary shared by personal,
commercial and Mayaki/Oko program use without sharing private data between spaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from app.application.personal_crm import (
    ChannelIdentityRecord,
    ConversationAttention,
    ConversationAttentionState,
    InteractionDirection,
    PersonalCrmDomainError,
    PersonalCrmExportV1,
    PersonalInteraction,
    PersonalNote,
    PersonalNoteKind,
    PersonalWorkItem,
    PersonalWorkItemStatus,
    PersonRecord,
    ResponsibleParty,
    SetConversationAttentionCommand,
    apply_attention_command,
)

RelationshipCrmDomainError = PersonalCrmDomainError


class RelationshipSpaceKind(StrEnum):
    """Privacy and product boundary for relationship data."""

    PERSONAL = "personal"
    COMMERCIAL = "commercial"
    PROGRAM = "program"


class RelationshipRole(StrEnum):
    PERSONAL_CONTACT = "personal_contact"
    FAMILY = "family"
    FRIEND = "friend"
    LEAD = "lead"
    CUSTOMER = "customer"
    PARTNER = "partner"
    CONTRACTOR = "contractor"
    TEAM_MEMBER = "team_member"
    PARTICIPANT = "participant"
    PROGRAM_MEMBER = "program_member"
    MENTOR = "mentor"
    OTHER = "other"


class RelationshipInteractionKind(StrEnum):
    MESSAGE = "message"
    CALL = "call"
    MEETING = "meeting"
    PAYMENT = "payment"
    AGREEMENT = "agreement"
    PROGRAM_EVENT = "program_event"
    OTHER = "other"


class DealStatus(StrEnum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"
    CANCELLED = "cancelled"


_ROLES_BY_SPACE: dict[RelationshipSpaceKind, frozenset[RelationshipRole]] = {
    RelationshipSpaceKind.PERSONAL: frozenset(
        {
            RelationshipRole.PERSONAL_CONTACT,
            RelationshipRole.FAMILY,
            RelationshipRole.FRIEND,
            RelationshipRole.OTHER,
        }
    ),
    RelationshipSpaceKind.COMMERCIAL: frozenset(
        {
            RelationshipRole.LEAD,
            RelationshipRole.CUSTOMER,
            RelationshipRole.PARTNER,
            RelationshipRole.CONTRACTOR,
            RelationshipRole.TEAM_MEMBER,
            RelationshipRole.OTHER,
        }
    ),
    RelationshipSpaceKind.PROGRAM: frozenset(
        {
            RelationshipRole.PARTICIPANT,
            RelationshipRole.PROGRAM_MEMBER,
            RelationshipRole.MENTOR,
            RelationshipRole.OTHER,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class RelationshipSpace:
    workspace_key: str
    space_key: str
    kind: RelationshipSpaceKind
    name: str = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.space_key, "space_key", 128)
        _require_text(self.name, "space_name", 255)
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class RelationshipRecord:
    workspace_key: str
    space_key: str
    space_kind: RelationshipSpaceKind
    relationship_key: str
    person_key: str
    role: RelationshipRole
    created_at: datetime
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_relationship_scope(self)
        _require_aware(self.created_at, "created_at")
        _require_optional_aware(self.ended_at, "ended_at")
        if self.ended_at is not None and self.ended_at < self.created_at:
            raise RelationshipCrmDomainError("relationship_ended_before_created")
        if self.role not in _ROLES_BY_SPACE[self.space_kind]:
            raise RelationshipCrmDomainError("relationship_role_space_mismatch")


@dataclass(frozen=True, slots=True)
class RelationshipInteraction:
    """Immutable semantic fact; it never changes attention or a deal stage."""

    workspace_key: str
    space_key: str
    relationship_key: str
    person_key: str
    interaction_key: str
    kind: RelationshipInteractionKind
    direction: InteractionDirection
    occurred_at: datetime
    summary: str | None = field(default=None, repr=False)
    source_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_relationship_scope(self)
        _require_token(self.interaction_key, "interaction_key", 128)
        _require_aware(self.occurred_at, "occurred_at")
        _require_optional_text(self.summary, "summary", 4096)
        _require_optional_token(self.source_ref, "source_ref", 255)


@dataclass(frozen=True, slots=True)
class PipelineRecord:
    workspace_key: str
    space_key: str
    space_kind: RelationshipSpaceKind
    pipeline_key: str
    name: str = field(repr=False)
    version: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_space_scope(self.workspace_key, self.space_key)
        _require_token(self.pipeline_key, "pipeline_key", 128)
        _require_text(self.name, "pipeline_name", 255)
        _require_aware(self.created_at, "created_at")
        if self.space_kind is RelationshipSpaceKind.PERSONAL:
            raise RelationshipCrmDomainError("pipeline_not_allowed_in_personal_space")
        if self.version < 1:
            raise RelationshipCrmDomainError("pipeline_version_invalid")


@dataclass(frozen=True, slots=True)
class PipelineStageRecord:
    workspace_key: str
    space_key: str
    pipeline_key: str
    stage_key: str
    name: str = field(repr=False)
    position: int
    pipeline_version: int

    def __post_init__(self) -> None:
        _require_space_scope(self.workspace_key, self.space_key)
        _require_token(self.pipeline_key, "pipeline_key", 128)
        _require_token(self.stage_key, "stage_key", 128)
        _require_text(self.name, "stage_name", 255)
        if self.position < 0:
            raise RelationshipCrmDomainError("stage_position_invalid")
        if self.pipeline_version < 1:
            raise RelationshipCrmDomainError("pipeline_version_invalid")


@dataclass(frozen=True, slots=True)
class DealRecord:
    workspace_key: str
    space_key: str
    space_kind: RelationshipSpaceKind
    deal_key: str
    relationship_key: str
    pipeline_key: str
    stage_key: str
    title: str
    status: DealStatus
    revision: int
    actor_key: str
    last_command_key: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    amount_microunits: int | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        _require_space_scope(self.workspace_key, self.space_key)
        _require_token(self.deal_key, "deal_key", 128)
        _require_token(self.relationship_key, "relationship_key", 128)
        _require_token(self.pipeline_key, "pipeline_key", 128)
        _require_token(self.stage_key, "stage_key", 128)
        _require_text(self.title, "deal_title", 512)
        _require_token(self.actor_key, "actor_key", 128)
        _require_token(self.last_command_key, "last_command_key", 128)
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        _require_optional_aware(self.closed_at, "closed_at")
        if self.space_kind is RelationshipSpaceKind.PERSONAL:
            raise RelationshipCrmDomainError("deal_not_allowed_in_personal_space")
        if self.revision < 1:
            raise RelationshipCrmDomainError("deal_revision_invalid")
        if self.updated_at < self.created_at:
            raise RelationshipCrmDomainError("deal_updated_before_created")
        if self.closed_at is not None and self.closed_at < self.created_at:
            raise RelationshipCrmDomainError("deal_closed_before_created")
        _require_deal_money(self.amount_microunits, self.currency)
        _require_deal_closure(self.status, self.closed_at)


@dataclass(frozen=True, slots=True)
class MoveDealStageCommand:
    command_key: str
    workspace_key: str
    space_key: str
    deal_key: str
    expected_revision: int
    stage_key: str
    status: DealStatus
    actor_key: str
    confirmed: bool
    occurred_at: datetime
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_token(self.command_key, "command_key", 128)
        _require_space_scope(self.workspace_key, self.space_key)
        _require_token(self.deal_key, "deal_key", 128)
        _require_token(self.stage_key, "stage_key", 128)
        _require_token(self.actor_key, "actor_key", 128)
        _require_aware(self.occurred_at, "occurred_at")
        _require_optional_aware(self.closed_at, "closed_at")
        if self.expected_revision < 1:
            raise RelationshipCrmDomainError("deal_expected_revision_invalid")
        if self.confirmed is not True:
            raise RelationshipCrmDomainError("deal_confirmation_required")
        _require_deal_closure(self.status, self.closed_at)


def apply_deal_stage_command(
    current: DealRecord,
    command: MoveDealStageCommand,
) -> DealRecord:
    """Apply an explicit, idempotent stage change; interactions cannot call this implicitly."""

    if (
        current.workspace_key,
        current.space_key,
        current.deal_key,
    ) != (
        command.workspace_key,
        command.space_key,
        command.deal_key,
    ):
        raise RelationshipCrmDomainError("deal_scope_mismatch")

    if current.last_command_key == command.command_key:
        if (
            current.stage_key != command.stage_key
            or current.status is not command.status
            or current.actor_key != command.actor_key
            or current.updated_at != command.occurred_at
            or current.closed_at != command.closed_at
            or command.expected_revision != current.revision - 1
        ):
            raise RelationshipCrmDomainError("deal_command_key_reused")
        return current

    if command.expected_revision != current.revision:
        raise RelationshipCrmDomainError("deal_revision_conflict")
    if command.occurred_at < current.updated_at:
        raise RelationshipCrmDomainError("deal_command_before_current_revision")

    return replace(
        current,
        stage_key=command.stage_key,
        status=command.status,
        revision=current.revision + 1,
        actor_key=command.actor_key,
        last_command_key=command.command_key,
        updated_at=command.occurred_at,
        closed_at=command.closed_at,
    )


def _require_relationship_scope(record: object) -> None:
    workspace_key = getattr(record, "workspace_key")
    space_key = getattr(record, "space_key")
    relationship_key = getattr(record, "relationship_key")
    person_key = getattr(record, "person_key")
    _require_space_scope(workspace_key, space_key)
    _require_token(relationship_key, "relationship_key", 128)
    _require_token(person_key, "person_key", 128)


def _require_space_scope(workspace_key: str, space_key: str) -> None:
    _require_token(workspace_key, "workspace_key", 128)
    _require_token(space_key, "space_key", 128)


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")


def _require_optional_token(value: str | None, field_name: str, maximum: int) -> None:
    if value is not None:
        _require_token(value, field_name, maximum)


def _require_text(value: str, field_name: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")


def _require_optional_text(value: str | None, field_name: str, maximum: int) -> None:
    if value is not None:
        _require_text(value, field_name, maximum)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RelationshipCrmDomainError(f"{field_name}_timezone_required")


def _require_optional_aware(value: datetime | None, field_name: str) -> None:
    if value is not None:
        _require_aware(value, field_name)


def _require_deal_money(amount_microunits: int | None, currency: str | None) -> None:
    if amount_microunits is None and currency is None:
        return
    if amount_microunits is None or currency is None:
        raise RelationshipCrmDomainError("deal_money_incomplete")
    if amount_microunits < 0:
        raise RelationshipCrmDomainError("deal_amount_invalid")
    if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise RelationshipCrmDomainError("deal_currency_invalid")


def _require_deal_closure(status: DealStatus, closed_at: datetime | None) -> None:
    if status is DealStatus.OPEN and closed_at is not None:
        raise RelationshipCrmDomainError("open_deal_cannot_be_closed")
    if status is not DealStatus.OPEN and closed_at is None:
        raise RelationshipCrmDomainError("closed_deal_requires_closed_at")


__all__ = [
    "ChannelIdentityRecord",
    "ConversationAttention",
    "ConversationAttentionState",
    "DealRecord",
    "DealStatus",
    "InteractionDirection",
    "MoveDealStageCommand",
    "PersonalCrmDomainError",
    "PersonalCrmExportV1",
    "PersonalInteraction",
    "PersonalNote",
    "PersonalNoteKind",
    "PersonalWorkItem",
    "PersonalWorkItemStatus",
    "PersonRecord",
    "PipelineRecord",
    "PipelineStageRecord",
    "RelationshipCrmDomainError",
    "RelationshipInteraction",
    "RelationshipInteractionKind",
    "RelationshipRecord",
    "RelationshipRole",
    "RelationshipSpace",
    "RelationshipSpaceKind",
    "ResponsibleParty",
    "SetConversationAttentionCommand",
    "apply_attention_command",
    "apply_deal_stage_command",
]
