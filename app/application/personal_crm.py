"""Portable pure-domain contracts for Gate's Personal CRM bounded context.

Transport facts, relationship notes, work items, and conversation attention are
deliberately separate.  In particular, message direction never implies whether a
conversation needs attention: only a confirmed attention command can change that
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Protocol


class PersonalCrmDomainError(ValueError):
    """A stable, machine-readable Personal CRM invariant violation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ConversationAttentionState(StrEnum):
    """Explicit owner intent; never derived from inbound/outbound direction."""

    UNTRIAGED = "untriaged"
    ACTION_ON_ME = "action_on_me"
    WAITING_ON_THEM = "waiting_on_them"
    SCHEDULED = "scheduled"
    NO_ACTION = "no_action"
    ARCHIVED = "archived"


class InteractionDirection(StrEnum):
    """A factual transport direction with no workflow semantics."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    SYSTEM = "system"


class PersonalNoteKind(StrEnum):
    NOTE = "note"
    THOUGHT = "thought"


class ResponsibleParty(StrEnum):
    WORKSPACE_OWNER = "workspace_owner"
    PERSON = "person"
    SHARED = "shared"
    UNASSIGNED = "unassigned"


class PersonalWorkItemStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PersonRecord:
    workspace_key: str
    person_key: str
    display_name: str = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        _require_scope(self.workspace_key, self.person_key)
        _require_text(self.display_name, "display_name", 255)
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ChannelIdentityRecord:
    """A channel address linked to, but not used as, a Person identity."""

    workspace_key: str
    person_key: str
    channel_identity_key: str
    channel: str
    connector_key: str
    external_subject_ref: str = field(repr=False)
    linked_at: datetime

    def __post_init__(self) -> None:
        _require_scope(self.workspace_key, self.person_key)
        _require_token(self.channel_identity_key, "channel_identity_key", 128)
        _require_token(self.channel, "channel", 64)
        _require_token(self.connector_key, "connector_key", 128)
        _require_text(self.external_subject_ref, "external_subject_ref", 512)
        _require_aware(self.linked_at, "linked_at")


@dataclass(frozen=True, slots=True)
class PersonalInteraction:
    """A semantic timeline fact; direction does not mutate attention."""

    workspace_key: str
    person_key: str
    conversation_key: str
    interaction_key: str
    channel_identity_key: str
    direction: InteractionDirection
    occurred_at: datetime
    summary: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_scope(self.workspace_key, self.person_key, self.conversation_key)
        _require_token(self.interaction_key, "interaction_key", 128)
        _require_token(self.channel_identity_key, "channel_identity_key", 128)
        _require_aware(self.occurred_at, "occurred_at")
        _require_optional_text(self.summary, "summary", 4096)


@dataclass(frozen=True, slots=True)
class SetConversationAttentionCommand:
    """The only command in this module that can change conversation attention."""

    command_key: str
    workspace_key: str
    person_key: str
    conversation_key: str
    state: ConversationAttentionState
    expected_revision: int
    actor_key: str
    confirmed: bool
    occurred_at: datetime
    next_review_at: datetime | None = None
    note: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_token(self.command_key, "command_key", 128)
        _require_scope(self.workspace_key, self.person_key, self.conversation_key)
        _require_token(self.actor_key, "actor_key", 128)
        if self.expected_revision < 0:
            raise PersonalCrmDomainError("attention_expected_revision_invalid")
        if self.confirmed is not True:
            raise PersonalCrmDomainError("attention_confirmation_required")
        _require_aware(self.occurred_at, "occurred_at")
        _require_optional_aware(self.next_review_at, "next_review_at")
        _require_optional_text(self.note, "note", 2048)
        if self.state is ConversationAttentionState.SCHEDULED and self.next_review_at is None:
            raise PersonalCrmDomainError("scheduled_attention_requires_next_review_at")


@dataclass(frozen=True, slots=True)
class ConversationAttention:
    workspace_key: str
    person_key: str
    conversation_key: str
    state: ConversationAttentionState
    revision: int
    actor_key: str
    last_command_key: str
    updated_at: datetime
    next_review_at: datetime | None = None
    note: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_scope(self.workspace_key, self.person_key, self.conversation_key)
        if self.revision < 1:
            raise PersonalCrmDomainError("attention_revision_invalid")
        _require_token(self.actor_key, "actor_key", 128)
        _require_token(self.last_command_key, "last_command_key", 128)
        _require_aware(self.updated_at, "updated_at")
        _require_optional_aware(self.next_review_at, "next_review_at")
        _require_optional_text(self.note, "note", 2048)


def apply_attention_command(
    current: ConversationAttention | None,
    command: SetConversationAttentionCommand,
) -> ConversationAttention:
    """Apply an explicit command with scope, replay, and optimistic-lock checks."""

    if current is not None:
        _require_same_scope(current, command)
        if current.last_command_key == command.command_key:
            if not _is_exact_replay(current, command):
                raise PersonalCrmDomainError("attention_command_key_reused")
            return current
        current_revision = current.revision
    else:
        current_revision = 0

    if command.expected_revision != current_revision:
        raise PersonalCrmDomainError("attention_revision_conflict")

    return ConversationAttention(
        workspace_key=command.workspace_key,
        person_key=command.person_key,
        conversation_key=command.conversation_key,
        state=command.state,
        revision=current_revision + 1,
        actor_key=command.actor_key,
        last_command_key=command.command_key,
        updated_at=command.occurred_at,
        next_review_at=command.next_review_at,
        note=command.note,
    )


@dataclass(frozen=True, slots=True)
class PersonalNote:
    """A note or thought about a person, independent of any reply task."""

    note_key: str
    workspace_key: str
    person_key: str
    kind: PersonalNoteKind
    body: str = field(repr=False)
    created_at: datetime
    conversation_key: str | None = None

    def __post_init__(self) -> None:
        _require_token(self.note_key, "note_key", 128)
        _require_scope(self.workspace_key, self.person_key, self.conversation_key)
        _require_text(self.body, "body", 16_384)
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class PersonalWorkItem:
    """A portable follow-up owned by a declared party and review time."""

    work_item_key: str
    workspace_key: str
    person_key: str
    title: str
    responsible_party: ResponsibleParty
    next_review_at: datetime
    status: PersonalWorkItemStatus
    created_at: datetime
    updated_at: datetime
    conversation_key: str | None = None
    details: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_token(self.work_item_key, "work_item_key", 128)
        _require_scope(self.workspace_key, self.person_key, self.conversation_key)
        _require_text(self.title, "title", 512)
        _require_aware(self.next_review_at, "next_review_at")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        _require_optional_text(self.details, "details", 16_384)
        if self.updated_at < self.created_at:
            raise PersonalCrmDomainError("work_item_updated_before_created")


class _PersonScoped(Protocol):
    @property
    def workspace_key(self) -> str: ...

    @property
    def person_key(self) -> str: ...


@dataclass(frozen=True, slots=True)
class PersonalCrmExportV1:
    """JSON-portable, deterministic transfer DTO for future module extraction."""

    SCHEMA_VERSION: ClassVar[str] = "gate.personal_crm.export.v1"

    workspace_key: str
    person: PersonRecord = field(repr=False)
    exported_at: datetime
    channel_identities: tuple[ChannelIdentityRecord, ...] = field(default=(), repr=False)
    interactions: tuple[PersonalInteraction, ...] = field(default=(), repr=False)
    attention: tuple[ConversationAttention, ...] = field(default=(), repr=False)
    notes: tuple[PersonalNote, ...] = field(default=(), repr=False)
    work_items: tuple[PersonalWorkItem, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_aware(self.exported_at, "exported_at")
        if self.person.workspace_key != self.workspace_key:
            raise PersonalCrmDomainError("export_workspace_scope_mismatch")
        for identity in self.channel_identities:
            _require_export_scope(self, identity)
        for interaction in self.interactions:
            _require_export_scope(self, interaction)
        for attention_record in self.attention:
            _require_export_scope(self, attention_record)
        for note_record in self.notes:
            _require_export_scope(self, note_record)
        for work_item in self.work_items:
            _require_export_scope(self, work_item)

        identity_keys = {item.channel_identity_key for item in self.channel_identities}
        for interaction in self.interactions:
            if interaction.channel_identity_key not in identity_keys:
                raise PersonalCrmDomainError("export_channel_identity_missing")

    def to_portable_dict(self) -> dict[str, object]:
        """Return only JSON-native values with stable ordering and UTC timestamps."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "workspace_key": self.workspace_key,
            "exported_at": _timestamp(self.exported_at),
            "person": {
                "person_key": self.person.person_key,
                "display_name": self.person.display_name,
                "created_at": _timestamp(self.person.created_at),
            },
            "channel_identities": [
                {
                    "channel_identity_key": item.channel_identity_key,
                    "channel": item.channel,
                    "connector_key": item.connector_key,
                    "external_subject_ref": item.external_subject_ref,
                    "linked_at": _timestamp(item.linked_at),
                }
                for item in sorted(
                    self.channel_identities, key=lambda value: value.channel_identity_key
                )
            ],
            "interactions": [
                {
                    "interaction_key": item.interaction_key,
                    "conversation_key": item.conversation_key,
                    "channel_identity_key": item.channel_identity_key,
                    "direction": item.direction.value,
                    "occurred_at": _timestamp(item.occurred_at),
                    "summary": item.summary,
                }
                for item in sorted(self.interactions, key=lambda value: value.interaction_key)
            ],
            "attention": [
                {
                    "conversation_key": item.conversation_key,
                    "state": item.state.value,
                    "revision": item.revision,
                    "actor_key": item.actor_key,
                    "last_command_key": item.last_command_key,
                    "updated_at": _timestamp(item.updated_at),
                    "next_review_at": _optional_timestamp(item.next_review_at),
                    "note": item.note,
                }
                for item in sorted(self.attention, key=lambda value: value.conversation_key)
            ],
            "notes": [
                {
                    "note_key": item.note_key,
                    "kind": item.kind.value,
                    "body": item.body,
                    "created_at": _timestamp(item.created_at),
                    "conversation_key": item.conversation_key,
                }
                for item in sorted(self.notes, key=lambda value: value.note_key)
            ],
            "work_items": [
                {
                    "work_item_key": item.work_item_key,
                    "title": item.title,
                    "responsible_party": item.responsible_party.value,
                    "next_review_at": _timestamp(item.next_review_at),
                    "status": item.status.value,
                    "created_at": _timestamp(item.created_at),
                    "updated_at": _timestamp(item.updated_at),
                    "conversation_key": item.conversation_key,
                    "details": item.details,
                }
                for item in sorted(self.work_items, key=lambda value: value.work_item_key)
            ],
        }


def _require_export_scope(export: PersonalCrmExportV1, record: _PersonScoped) -> None:
    if record.workspace_key != export.workspace_key:
        raise PersonalCrmDomainError("export_workspace_scope_mismatch")
    if record.person_key != export.person.person_key:
        raise PersonalCrmDomainError("export_person_scope_mismatch")


def _require_same_scope(
    current: ConversationAttention,
    command: SetConversationAttentionCommand,
) -> None:
    if (
        current.workspace_key,
        current.person_key,
        current.conversation_key,
    ) != (
        command.workspace_key,
        command.person_key,
        command.conversation_key,
    ):
        raise PersonalCrmDomainError("attention_scope_mismatch")


def _is_exact_replay(
    current: ConversationAttention,
    command: SetConversationAttentionCommand,
) -> bool:
    return (
        current.state is command.state
        and current.actor_key == command.actor_key
        and current.updated_at == command.occurred_at
        and current.next_review_at == command.next_review_at
        and current.note == command.note
        and command.expected_revision == current.revision - 1
    )


def _require_scope(
    workspace_key: str,
    person_key: str,
    conversation_key: str | None = None,
) -> None:
    _require_token(workspace_key, "workspace_key", 128)
    _require_token(person_key, "person_key", 128)
    if conversation_key is not None:
        _require_token(conversation_key, "conversation_key", 128)


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise PersonalCrmDomainError(f"{field_name}_invalid")


def _require_text(value: str, field_name: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise PersonalCrmDomainError(f"{field_name}_invalid")


def _require_optional_text(value: str | None, field_name: str, maximum: int) -> None:
    if value is not None:
        _require_text(value, field_name, maximum)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PersonalCrmDomainError(f"{field_name}_timezone_required")


def _require_optional_aware(value: datetime | None, field_name: str) -> None:
    if value is not None:
        _require_aware(value, field_name)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else _timestamp(value)
