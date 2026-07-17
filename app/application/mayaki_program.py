"""Portable, network-free Mayaki/Oko program and human-handoff contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal

from app.application.relationship_crm import (
    RelationshipCrmDomainError,
    RelationshipSpaceKind,
)

MAYAKI_PROGRAM_EVENT_SCHEMA: Literal["mayaki.program.event.v1"] = (
    "mayaki.program.event.v1"
)


class ProgramState(StrEnum):
    PENDING_CONSENT = "pending_consent"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    HANDOFF_PENDING = "handoff_pending"
    HANDED_OFF = "handed_off"
    REVOKED = "revoked"


class ProgramEventKind(StrEnum):
    CONSENT_GRANTED = "consent_granted"
    PRACTICE_ASSIGNED = "practice_assigned"
    PRACTICE_COMPLETED = "practice_completed"
    PAUSED = "paused"
    RESUMED = "resumed"
    COMPLETED = "completed"
    HUMAN_HANDOFF_REQUESTED = "human_handoff_requested"
    HUMAN_HANDOFF_ACCEPTED = "human_handoff_accepted"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ProgramSession:
    workspace_key: str
    space_key: str
    program_key: str
    participant_key: str
    state: ProgramState
    revision: int
    actor_key: str
    last_event_key: str
    last_event_kind: ProgramEventKind
    updated_at: datetime
    space_kind: RelationshipSpaceKind = RelationshipSpaceKind.PROGRAM

    def __post_init__(self) -> None:
        for value, name in (
            (self.workspace_key, "program_workspace_key"),
            (self.space_key, "program_space_key"),
            (self.program_key, "program_key"),
            (self.participant_key, "program_participant_key"),
            (self.actor_key, "program_actor_key"),
            (self.last_event_key, "program_event_key"),
        ):
            _token(value, name)
        if self.space_kind is not RelationshipSpaceKind.PROGRAM:
            raise RelationshipCrmDomainError("program_space_kind_invalid")
        if self.revision < 1:
            raise RelationshipCrmDomainError("program_revision_invalid")
        _aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class ApplyProgramEventCommand:
    event_key: str
    workspace_key: str
    space_key: str
    program_key: str
    participant_key: str
    kind: ProgramEventKind
    expected_revision: int
    actor_key: str
    confirmed: bool
    occurred_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_key, "program_event_key"),
            (self.workspace_key, "program_workspace_key"),
            (self.space_key, "program_space_key"),
            (self.program_key, "program_key"),
            (self.participant_key, "program_participant_key"),
            (self.actor_key, "program_actor_key"),
        ):
            _token(value, name)
        if self.expected_revision < 0:
            raise RelationshipCrmDomainError("program_expected_revision_invalid")
        if self.confirmed is not True:
            raise RelationshipCrmDomainError("program_event_confirmation_required")
        _aware(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ProgramEventResult:
    session: ProgramSession
    event: MayakiProgramEventV1
    notification_allowed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        if self.notification_allowed or self.telegram_delivery_allowed:
            raise RelationshipCrmDomainError("program_event_cannot_grant_delivery")


@dataclass(frozen=True, slots=True)
class MayakiProgramEventV1:
    event_key: str
    workspace_key: str
    space_key: str
    program_key: str
    participant_key: str
    kind: ProgramEventKind
    revision: int
    occurred_at: datetime
    schema: Literal["mayaki.program.event.v1"] = MAYAKI_PROGRAM_EVENT_SCHEMA
    source: Literal["oko_fixture"] = "oko_fixture"

    def __post_init__(self) -> None:
        if self.schema != MAYAKI_PROGRAM_EVENT_SCHEMA:
            raise RelationshipCrmDomainError("program_event_schema_invalid")
        if self.revision < 1:
            raise RelationshipCrmDomainError("program_event_revision_invalid")


def apply_program_event(
    current: ProgramSession | None,
    command: ApplyProgramEventCommand,
) -> ProgramEventResult:
    """Apply one explicit event without notifications, Telegram or implicit consent."""

    if current is None:
        if command.kind is not ProgramEventKind.CONSENT_GRANTED:
            raise RelationshipCrmDomainError("program_consent_required")
        if command.expected_revision != 0:
            raise RelationshipCrmDomainError("program_revision_conflict")
        updated = ProgramSession(
            workspace_key=command.workspace_key,
            space_key=command.space_key,
            program_key=command.program_key,
            participant_key=command.participant_key,
            state=ProgramState.ACTIVE,
            revision=1,
            actor_key=command.actor_key,
            last_event_key=command.event_key,
            last_event_kind=command.kind,
            updated_at=command.occurred_at,
        )
        return _result(updated, command)

    _same_scope(current, command)
    if current.last_event_key == command.event_key:
        if (
            current.revision != command.expected_revision + 1
            or current.actor_key != command.actor_key
            or current.updated_at != command.occurred_at
            or current.last_event_kind is not command.kind
        ):
            raise RelationshipCrmDomainError("program_event_key_reused")
        return _result(current, command)
    if current.revision != command.expected_revision:
        raise RelationshipCrmDomainError("program_revision_conflict")
    if command.occurred_at < current.updated_at:
        raise RelationshipCrmDomainError("program_event_time_regression")

    updated = replace(
        current,
        state=_next_state(current.state, command.kind),
        revision=current.revision + 1,
        actor_key=command.actor_key,
        last_event_key=command.event_key,
        last_event_kind=command.kind,
        updated_at=command.occurred_at,
    )
    return _result(updated, command)


def _next_state(current: ProgramState, kind: ProgramEventKind) -> ProgramState:
    transitions = {
        (
            ProgramState.ACTIVE,
            ProgramEventKind.PRACTICE_ASSIGNED,
        ): ProgramState.ACTIVE,
        (
            ProgramState.ACTIVE,
            ProgramEventKind.PRACTICE_COMPLETED,
        ): ProgramState.ACTIVE,
        (ProgramState.ACTIVE, ProgramEventKind.PAUSED): ProgramState.PAUSED,
        (ProgramState.PAUSED, ProgramEventKind.RESUMED): ProgramState.ACTIVE,
        (ProgramState.ACTIVE, ProgramEventKind.COMPLETED): ProgramState.COMPLETED,
        (
            ProgramState.ACTIVE,
            ProgramEventKind.HUMAN_HANDOFF_REQUESTED,
        ): ProgramState.HANDOFF_PENDING,
        (
            ProgramState.PAUSED,
            ProgramEventKind.HUMAN_HANDOFF_REQUESTED,
        ): ProgramState.HANDOFF_PENDING,
        (
            ProgramState.HANDOFF_PENDING,
            ProgramEventKind.HUMAN_HANDOFF_ACCEPTED,
        ): ProgramState.HANDED_OFF,
    }
    if kind is ProgramEventKind.REVOKED and current not in {
        ProgramState.HANDED_OFF,
        ProgramState.REVOKED,
    }:
        return ProgramState.REVOKED
    result = transitions.get((current, kind))
    if result is None:
        raise RelationshipCrmDomainError("program_transition_invalid")
    return result


def _result(
    session: ProgramSession,
    command: ApplyProgramEventCommand,
) -> ProgramEventResult:
    return ProgramEventResult(
        session=session,
        event=MayakiProgramEventV1(
            event_key=command.event_key,
            workspace_key=command.workspace_key,
            space_key=command.space_key,
            program_key=command.program_key,
            participant_key=command.participant_key,
            kind=command.kind,
            revision=session.revision,
            occurred_at=command.occurred_at,
        ),
    )


def _same_scope(
    current: ProgramSession,
    command: ApplyProgramEventCommand,
) -> None:
    if (
        current.workspace_key,
        current.space_key,
        current.program_key,
        current.participant_key,
    ) != (
        command.workspace_key,
        command.space_key,
        command.program_key,
        command.participant_key,
    ):
        raise RelationshipCrmDomainError("program_scope_mismatch")


def _token(value: str, field_name: str) -> None:
    if not value or value != value.strip() or len(value) > 128:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RelationshipCrmDomainError("program_time_timezone_required")
