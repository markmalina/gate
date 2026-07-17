"""Write-free Gate-to-Relationship-CRM projection tests."""

from datetime import UTC, datetime, timedelta

from app.application.relationship_crm import (
    ConversationAttentionState,
    InteractionDirection,
)
from app.application.relationship_crm_projection import (
    GateConversationProjectionFact,
    dry_run_gate_backfill,
    project_gate_conversation,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def fact(
    *,
    interaction_ref: str = "gate-message-ref-1",
    direction: InteractionDirection = InteractionDirection.INBOUND,
    display_name: str = "Anna Private",
) -> GateConversationProjectionFact:
    return GateConversationProjectionFact(
        workspace_key="mark-personal",
        person_key="person-anna",
        display_name=display_name,
        conversation_ref="gate-conversation-ref-1",
        interaction_ref=interaction_ref,
        direction=direction,
        occurred_at=NOW,
    )


def test_direction_never_guesses_attention_state() -> None:
    inbound = project_gate_conversation(fact())
    outbound = project_gate_conversation(
        fact(
            interaction_ref="gate-message-ref-2",
            direction=InteractionDirection.OUTBOUND,
        )
    )

    assert inbound.attention.state is ConversationAttentionState.UNTRIAGED
    assert outbound.attention.state is ConversationAttentionState.UNTRIAGED
    assert inbound.interaction.direction is InteractionDirection.INBOUND
    assert outbound.interaction.direction is InteractionDirection.OUTBOUND


def test_projection_excludes_message_text_and_provider_identifiers() -> None:
    plan = project_gate_conversation(fact())
    rendered = repr(plan)

    assert plan.interaction.summary is None
    assert plan.interaction.source_ref == plan.interaction.interaction_key
    assert "Anna Private" not in rendered
    assert "telegram_chat_id" not in rendered
    assert "provider_message_id" not in rendered


def test_dry_run_is_deterministic_deduplicated_and_write_free() -> None:
    item = fact()
    report = dry_run_gate_backfill((item, item))

    assert report.input_count == 2
    assert report.planned_count == 1
    assert report.duplicate_count == 1
    assert report.conflict_count == 0
    assert report.workspace_count == 1
    assert report.attention_state is ConversationAttentionState.UNTRIAGED
    assert report.writes_performed is False
    assert len(report.projection_sha256) == 64
    assert report == dry_run_gate_backfill((item, item))


def test_reused_source_reference_with_different_fact_is_reported_as_conflict() -> None:
    original = fact()
    conflicting = GateConversationProjectionFact(
        workspace_key=original.workspace_key,
        person_key=original.person_key,
        display_name="Changed",
        conversation_ref=original.conversation_ref,
        interaction_ref=original.interaction_ref,
        direction=original.direction,
        occurred_at=original.occurred_at + timedelta(seconds=1),
    )

    report = dry_run_gate_backfill((original, conflicting))

    assert report.planned_count == 1
    assert report.conflict_count == 1
    assert report.writes_performed is False


def test_projection_keys_are_stable_and_provider_opaque() -> None:
    first = project_gate_conversation(fact())
    second = project_gate_conversation(fact())

    assert first.relationship.relationship_key == second.relationship.relationship_key
    assert first.interaction.interaction_key == second.interaction.interaction_key
    assert "gate-message-ref-1" not in first.interaction.interaction_key
    assert "gate-conversation-ref-1" not in first.attention.conversation_key
