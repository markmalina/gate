"""Deterministic Oko program route with no provider, persistence or delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256

from app.application.mayaki_program import (
    ApplyProgramEventCommand,
    MayakiProgramEventV1,
    ProgramEventKind,
    ProgramSession,
    ProgramState,
    apply_program_event,
)
from app.application.relationship_crm import RelationshipCrmDomainError


@dataclass(frozen=True, slots=True)
class RunOkoFixtureRouteCommand:
    route_key: str
    workspace_key: str
    space_key: str
    program_key: str
    participant_key: str = field(repr=False)
    started_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.route_key, "oko_fixture_route_key"),
            (self.workspace_key, "oko_fixture_workspace_key"),
            (self.space_key, "oko_fixture_space_key"),
            (self.program_key, "oko_fixture_program_key"),
            (self.participant_key, "oko_fixture_participant_key"),
        ):
            if not value or value != value.strip() or len(value) > 128:
                raise RelationshipCrmDomainError(f"{name}_invalid")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise RelationshipCrmDomainError("oko_fixture_time_timezone_required")


@dataclass(frozen=True, slots=True)
class OkoFixtureRouteResult:
    session: ProgramSession = field(repr=False)
    events: tuple[MayakiProgramEventV1, ...] = field(repr=False)
    operation_ref: str = field(repr=False)
    provider_network_used: bool = False
    persistence_used: bool = False
    notification_allowed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        if self.session.state is not ProgramState.HANDED_OFF:
            raise RelationshipCrmDomainError("oko_fixture_handoff_incomplete")
        if len(self.events) != 5:
            raise RelationshipCrmDomainError("oko_fixture_event_count_invalid")
        if any(
            (
                self.provider_network_used,
                self.persistence_used,
                self.notification_allowed,
                self.telegram_delivery_allowed,
            )
        ):
            raise RelationshipCrmDomainError("oko_fixture_side_effect_forbidden")


class DeterministicOkoFixtureRunner:
    """Run consent → practice → result → handoff entirely in memory."""

    def run(self, command: RunOkoFixtureRouteCommand) -> OkoFixtureRouteResult:
        operation_ref = _operation_ref(command)
        current: ProgramSession | None = None
        events: list[MayakiProgramEventV1] = []
        kinds = (
            ProgramEventKind.CONSENT_GRANTED,
            ProgramEventKind.PRACTICE_ASSIGNED,
            ProgramEventKind.PRACTICE_COMPLETED,
            ProgramEventKind.HUMAN_HANDOFF_REQUESTED,
            ProgramEventKind.HUMAN_HANDOFF_ACCEPTED,
        )
        for index, kind in enumerate(kinds):
            result = apply_program_event(
                current,
                ApplyProgramEventCommand(
                    event_key=f"{operation_ref}:{index + 1}",
                    workspace_key=command.workspace_key,
                    space_key=command.space_key,
                    program_key=command.program_key,
                    participant_key=command.participant_key,
                    kind=kind,
                    expected_revision=index,
                    actor_key="oko-fixture",
                    confirmed=True,
                    occurred_at=command.started_at + timedelta(seconds=index),
                ),
            )
            current = result.session
            events.append(result.event)
        if current is None:
            raise RelationshipCrmDomainError("oko_fixture_session_missing")
        return OkoFixtureRouteResult(
            session=current,
            events=tuple(events),
            operation_ref=operation_ref,
        )


def _operation_ref(command: RunOkoFixtureRouteCommand) -> str:
    material = "\n".join(
        (
            command.route_key,
            command.workspace_key,
            command.space_key,
            command.program_key,
            command.participant_key,
        )
    )
    return f"oko-fixture-{sha256(material.encode()).hexdigest()[:24]}"
