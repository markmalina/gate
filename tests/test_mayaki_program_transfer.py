"""Mayaki transfer import is replayable, isolated and write-free."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.mayaki_program import (
    ApplyProgramEventCommand,
    MayakiProgramEventV1,
    ProgramEventKind,
    ProgramSession,
    ProgramState,
    apply_program_event,
)
from app.application.mayaki_program_transfer import (
    MayakiProgramTransferV1,
    build_mayaki_program_transfer,
    dry_run_mayaki_program_import,
)
from app.application.relationship_crm import RelationshipCrmDomainError

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def events() -> tuple[MayakiProgramEventV1, ...]:
    current: ProgramSession | None = None
    result: list[MayakiProgramEventV1] = []
    for index, kind in enumerate(
        (
            ProgramEventKind.CONSENT_GRANTED,
            ProgramEventKind.PRACTICE_ASSIGNED,
            ProgramEventKind.PRACTICE_COMPLETED,
        )
    ):
        applied = apply_program_event(
            current,
            ApplyProgramEventCommand(
                event_key=f"event-{index + 1}",
                workspace_key="mark",
                space_key="mayaki-program",
                program_key="oko-practice-v1",
                participant_key="participant-sensitive",
                kind=kind,
                expected_revision=index,
                actor_key="fixture",
                confirmed=True,
                occurred_at=NOW + timedelta(seconds=index),
            ),
        )
        current = applied.session
        result.append(applied.event)
    return tuple(result)


def transfer() -> MayakiProgramTransferV1:
    return build_mayaki_program_transfer(
        workspace_key="mark",
        space_key="mayaki-program",
        program_key="oko-practice-v1",
        participant_key="participant-sensitive",
        events=events(),
        generated_at=NOW + timedelta(minutes=1),
    )


def test_transfer_replays_to_the_same_state_without_writes() -> None:
    result = dry_run_mayaki_program_import(transfer())

    assert result.event_count == 3
    assert result.final_state is ProgramState.ACTIVE
    assert result.final_revision == 3
    assert result.writes_performed is False
    assert result.notification_allowed is False
    assert result.telegram_delivery_allowed is False


def test_transfer_rejects_cross_workspace_event() -> None:
    original = transfer()
    leaked = replace(original.events[1], workspace_key="other")

    with pytest.raises(
        RelationshipCrmDomainError,
        match="mayaki_transfer_scope_mismatch",
    ):
        build_mayaki_program_transfer(
            workspace_key=original.workspace_key,
            space_key=original.space_key,
            program_key=original.program_key,
            participant_key="participant-sensitive",
            events=(original.events[0], leaked, original.events[2]),
            generated_at=original.generated_at,
        )


def test_transfer_rejects_duplicate_event_keys() -> None:
    original = transfer()
    duplicate = replace(
        original.events[1],
        event_key=original.events[0].event_key,
    )
    rebuilt = build_mayaki_program_transfer(
        workspace_key=original.workspace_key,
        space_key=original.space_key,
        program_key=original.program_key,
        participant_key="participant-sensitive",
        events=(original.events[0], duplicate, original.events[2]),
        generated_at=original.generated_at,
    )

    with pytest.raises(
        RelationshipCrmDomainError,
        match="mayaki_transfer_event_key_duplicate",
    ):
        dry_run_mayaki_program_import(rebuilt)


def test_transfer_rejects_revision_gaps_before_replay() -> None:
    original = transfer()
    gap = replace(original.events[1], revision=7)
    rebuilt = build_mayaki_program_transfer(
        workspace_key=original.workspace_key,
        space_key=original.space_key,
        program_key=original.program_key,
        participant_key="participant-sensitive",
        events=(original.events[0], gap, original.events[2]),
        generated_at=original.generated_at,
    )

    with pytest.raises(
        RelationshipCrmDomainError,
        match="mayaki_transfer_revision_sequence_invalid",
    ):
        dry_run_mayaki_program_import(rebuilt)


def test_transfer_repr_hides_events_and_participant() -> None:
    value = transfer()
    rendered = repr(value)

    assert "participant-sensitive" not in rendered
    assert "event-1" not in rendered
    assert len(value.evidence_sha256) == 64
