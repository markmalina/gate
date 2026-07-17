"""End-to-end Oko dry runtime stays deterministic and side-effect free."""

from datetime import UTC, datetime
from pathlib import Path

from app.application.mayaki_program import ProgramEventKind, ProgramState
from app.application.oko_fixture_runtime import (
    DeterministicOkoFixtureRunner,
    RunOkoFixtureRouteCommand,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def command() -> RunOkoFixtureRouteCommand:
    return RunOkoFixtureRouteCommand(
        route_key="route-1",
        workspace_key="mark",
        space_key="mayaki-program",
        program_key="oko-practice-v1",
        participant_key="participant-sensitive",
        started_at=NOW,
    )


def test_fixture_reaches_human_handoff_after_one_practice() -> None:
    result = DeterministicOkoFixtureRunner().run(command())

    assert result.session.state is ProgramState.HANDED_OFF
    assert result.session.revision == 5
    assert tuple(event.kind for event in result.events) == (
        ProgramEventKind.CONSENT_GRANTED,
        ProgramEventKind.PRACTICE_ASSIGNED,
        ProgramEventKind.PRACTICE_COMPLETED,
        ProgramEventKind.HUMAN_HANDOFF_REQUESTED,
        ProgramEventKind.HUMAN_HANDOFF_ACCEPTED,
    )
    assert result.provider_network_used is False
    assert result.persistence_used is False
    assert result.notification_allowed is False
    assert result.telegram_delivery_allowed is False


def test_fixture_is_deterministic_for_the_same_route() -> None:
    runner = DeterministicOkoFixtureRunner()

    first = runner.run(command())
    second = runner.run(command())

    assert first == second
    assert first.operation_ref.startswith("oko-fixture-")


def test_fixture_repr_hides_participant_and_operation_reference() -> None:
    result = DeterministicOkoFixtureRunner().run(command())
    rendered = f"{command()!r} {result!r}"

    assert "participant-sensitive" not in rendered
    assert result.operation_ref not in rendered


def test_fixture_source_has_no_network_database_or_delivery_adapter() -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "application"
        / "oko_fixture_runtime.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "import openai",
        "import httpx",
        "AsyncSession",
        "send_message(",
    ):
        assert forbidden not in source
