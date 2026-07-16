"""Contracts for the standalone, space-scoped Relationship CRM kernel."""

from datetime import UTC, datetime, timedelta

import pytest
from app.application.relationship_crm import (
    DealRecord,
    DealStatus,
    InteractionDirection,
    MoveDealStageCommand,
    PipelineRecord,
    RelationshipCrmDomainError,
    RelationshipInteraction,
    RelationshipInteractionKind,
    RelationshipRecord,
    RelationshipRole,
    RelationshipSpace,
    RelationshipSpaceKind,
    apply_deal_stage_command,
)

NOW = datetime(2026, 7, 16, 18, 0, tzinfo=UTC)


def test_personal_commercial_and_program_spaces_are_explicit() -> None:
    spaces = {
        RelationshipSpace(
            workspace_key="mark",
            space_key=f"space-{kind.value}",
            kind=kind,
            name=kind.value,
            created_at=NOW,
        ).kind
        for kind in RelationshipSpaceKind
    }
    assert spaces == {
        RelationshipSpaceKind.PERSONAL,
        RelationshipSpaceKind.COMMERCIAL,
        RelationshipSpaceKind.PROGRAM,
    }


def test_roles_cannot_cross_space_privacy_boundary() -> None:
    personal = RelationshipRecord(
        workspace_key="mark",
        space_key="personal",
        space_kind=RelationshipSpaceKind.PERSONAL,
        relationship_key="rel-anna",
        person_key="person-anna",
        role=RelationshipRole.FRIEND,
        created_at=NOW,
    )
    assert personal.role is RelationshipRole.FRIEND

    with pytest.raises(
        RelationshipCrmDomainError,
        match="relationship_role_space_mismatch",
    ):
        RelationshipRecord(
            workspace_key="mark",
            space_key="personal",
            space_kind=RelationshipSpaceKind.PERSONAL,
            relationship_key="rel-anna-sales",
            person_key="person-anna",
            role=RelationshipRole.LEAD,
            created_at=NOW,
        )


def test_message_direction_is_only_an_interaction_fact() -> None:
    inbound = RelationshipInteraction(
        workspace_key="mayaki",
        space_key="commercial",
        relationship_key="rel-client",
        person_key="person-client",
        interaction_key="interaction-message-1",
        kind=RelationshipInteractionKind.MESSAGE,
        direction=InteractionDirection.INBOUND,
        occurred_at=NOW,
        summary="Asked about the program",
    )
    outbound = RelationshipInteraction(
        workspace_key="mayaki",
        space_key="commercial",
        relationship_key="rel-client",
        person_key="person-client",
        interaction_key="interaction-message-2",
        kind=RelationshipInteractionKind.MESSAGE,
        direction=InteractionDirection.OUTBOUND,
        occurred_at=NOW + timedelta(minutes=1),
        summary="Acknowledged and waiting for details",
    )

    assert inbound.direction is InteractionDirection.INBOUND
    assert outbound.direction is InteractionDirection.OUTBOUND
    assert not hasattr(outbound, "attention")
    assert not hasattr(outbound, "deal_stage")


def _deal() -> DealRecord:
    return DealRecord(
        workspace_key="mayaki",
        space_key="commercial",
        space_kind=RelationshipSpaceKind.COMMERCIAL,
        deal_key="deal-1",
        relationship_key="rel-client",
        pipeline_key="sales-v1",
        stage_key="lead",
        title="Mayaki program",
        status=DealStatus.OPEN,
        revision=1,
        actor_key="owner-mark",
        last_command_key="create-deal-1",
        created_at=NOW,
        updated_at=NOW,
        amount_microunits=500_000_000,
        currency="USD",
    )


def test_deal_stage_changes_only_by_confirmed_idempotent_command() -> None:
    with pytest.raises(RelationshipCrmDomainError, match="deal_confirmation_required"):
        MoveDealStageCommand(
            command_key="move-1",
            workspace_key="mayaki",
            space_key="commercial",
            deal_key="deal-1",
            expected_revision=1,
            stage_key="qualified",
            status=DealStatus.OPEN,
            actor_key="owner-mark",
            confirmed=False,
            occurred_at=NOW + timedelta(hours=1),
        )

    command = MoveDealStageCommand(
        command_key="move-1",
        workspace_key="mayaki",
        space_key="commercial",
        deal_key="deal-1",
        expected_revision=1,
        stage_key="qualified",
        status=DealStatus.OPEN,
        actor_key="owner-mark",
        confirmed=True,
        occurred_at=NOW + timedelta(hours=1),
    )
    moved = apply_deal_stage_command(_deal(), command)
    assert moved.stage_key == "qualified"
    assert moved.revision == 2
    assert apply_deal_stage_command(moved, command) is moved

    with pytest.raises(RelationshipCrmDomainError, match="deal_revision_conflict"):
        apply_deal_stage_command(
            moved,
            MoveDealStageCommand(
                command_key="move-stale",
                workspace_key="mayaki",
                space_key="commercial",
                deal_key="deal-1",
                expected_revision=1,
                stage_key="proposal",
                status=DealStatus.OPEN,
                actor_key="owner-mark",
                confirmed=True,
                occurred_at=NOW + timedelta(hours=2),
            ),
        )


def test_personal_space_cannot_own_pipeline_or_deal() -> None:
    with pytest.raises(
        RelationshipCrmDomainError,
        match="pipeline_not_allowed_in_personal_space",
    ):
        PipelineRecord(
            workspace_key="mark",
            space_key="personal",
            space_kind=RelationshipSpaceKind.PERSONAL,
            pipeline_key="private-sales",
            name="Not allowed",
            version=1,
            created_at=NOW,
        )

    with pytest.raises(
        RelationshipCrmDomainError,
        match="deal_not_allowed_in_personal_space",
    ):
        DealRecord(
            workspace_key="mark",
            space_key="personal",
            space_kind=RelationshipSpaceKind.PERSONAL,
            deal_key="deal-private",
            relationship_key="rel-friend",
            pipeline_key="pipeline",
            stage_key="lead",
            title="Wrong boundary",
            status=DealStatus.OPEN,
            revision=1,
            actor_key="owner-mark",
            last_command_key="create",
            created_at=NOW,
            updated_at=NOW,
        )
