"""Fixture-only Mayaki/Oko program and handoff state-machine tests."""

from datetime import UTC, datetime, timedelta

import pytest
from app.application.mayaki_program import (
    ApplyProgramEventCommand,
    ProgramEventKind,
    ProgramSession,
    ProgramState,
    apply_program_event,
)
from app.application.relationship_crm import RelationshipCrmDomainError

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def command(
    kind: ProgramEventKind,
    *,
    key: str,
    revision: int,
    occurred_at: datetime = NOW,
    workspace: str = "mark",
) -> ApplyProgramEventCommand:
    return ApplyProgramEventCommand(
        event_key=key,
        workspace_key=workspace,
        space_key="mayaki-program",
        program_key="oko-practice-v1",
        participant_key="participant-1",
        kind=kind,
        expected_revision=revision,
        actor_key="fixture-owner",
        confirmed=True,
        occurred_at=occurred_at,
    )


def active() -> ProgramSession:
    return apply_program_event(
        None,
        command(
            ProgramEventKind.CONSENT_GRANTED,
            key="consent-1",
            revision=0,
        ),
    ).session


def test_program_cannot_start_without_explicit_consent() -> None:
    with pytest.raises(
        RelationshipCrmDomainError,
        match="program_consent_required",
    ):
        apply_program_event(
            None,
            command(
                ProgramEventKind.HUMAN_HANDOFF_REQUESTED,
                key="handoff-early",
                revision=0,
            ),
        )


def test_handoff_requires_request_then_human_acceptance() -> None:
    started = active()
    requested = apply_program_event(
        started,
        command(
            ProgramEventKind.HUMAN_HANDOFF_REQUESTED,
            key="handoff-request",
            revision=1,
            occurred_at=NOW + timedelta(seconds=1),
        ),
    )
    accepted = apply_program_event(
        requested.session,
        command(
            ProgramEventKind.HUMAN_HANDOFF_ACCEPTED,
            key="handoff-accepted",
            revision=2,
            occurred_at=NOW + timedelta(seconds=2),
        ),
    )

    assert requested.session.state is ProgramState.HANDOFF_PENDING
    assert accepted.session.state is ProgramState.HANDED_OFF
    assert requested.notification_allowed is False
    assert accepted.telegram_delivery_allowed is False


def test_event_contract_is_stable_and_content_free() -> None:
    result = apply_program_event(
        None,
        command(
            ProgramEventKind.CONSENT_GRANTED,
            key="consent-contract",
            revision=0,
        ),
    )

    assert result.event.schema == "mayaki.program.event.v1"
    assert result.event.source == "oko_fixture"
    assert not hasattr(result.event, "message_text")
    assert not hasattr(result.event, "telegram_chat_id")


def test_exact_event_replay_is_idempotent() -> None:
    initial_command = command(
        ProgramEventKind.CONSENT_GRANTED,
        key="consent-replay",
        revision=0,
    )
    first = apply_program_event(None, initial_command)
    replay = apply_program_event(first.session, initial_command)

    assert replay == first


def test_cross_workspace_event_fails_closed() -> None:
    with pytest.raises(
        RelationshipCrmDomainError,
        match="program_scope_mismatch",
    ):
        apply_program_event(
            active(),
            command(
                ProgramEventKind.PAUSED,
                key="pause-other",
                revision=1,
                workspace="other",
            ),
        )


def test_invalid_handoff_acceptance_without_request_is_rejected() -> None:
    with pytest.raises(
        RelationshipCrmDomainError,
        match="program_transition_invalid",
    ):
        apply_program_event(
            active(),
            command(
                ProgramEventKind.HUMAN_HANDOFF_ACCEPTED,
                key="handoff-without-request",
                revision=1,
            ),
        )
