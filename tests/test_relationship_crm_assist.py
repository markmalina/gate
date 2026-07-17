"""Relationship CRM assist remains provenance-backed and owner-controlled."""

from datetime import UTC, datetime, timedelta

import pytest
from app.application.relationship_crm import RelationshipCrmDomainError
from app.application.relationship_crm_assist import (
    DecideRelationshipSuggestionCommand,
    RelationshipCrmSuggestion,
    RelationshipSuggestionEvidence,
    RelationshipSuggestionKind,
    RelationshipSuggestionState,
    decide_relationship_suggestion,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def suggestion() -> RelationshipCrmSuggestion:
    return RelationshipCrmSuggestion(
        suggestion_key="suggestion-1",
        workspace_key="mark",
        space_key="personal",
        relationship_key="anna-personal",
        kind=RelationshipSuggestionKind.FOLLOW_UP,
        proposed_value="Follow up next week",
        confidence_basis_points=8_200,
        evidence=(
            RelationshipSuggestionEvidence(
                source_kind="sanitized_interaction",
                source_ref="opaque-source-sensitive",
                observed_at=NOW,
            ),
        ),
        model_revision="fixture-v1",
        state=RelationshipSuggestionState.PENDING,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def decision(
    *,
    accepted: bool = True,
    workspace: str = "mark",
) -> DecideRelationshipSuggestionCommand:
    return DecideRelationshipSuggestionCommand(
        decision_key="decision-1",
        workspace_key=workspace,
        space_key="personal",
        suggestion_key="suggestion-1",
        expected_revision=1,
        accepted=accepted,
        actor_key="owner",
        confirmed=True,
        occurred_at=NOW + timedelta(seconds=1),
    )


def test_suggestion_requires_bounded_evidence_and_confidence() -> None:
    with pytest.raises(
        RelationshipCrmDomainError,
        match="suggestion_evidence_invalid",
    ):
        RelationshipCrmSuggestion(
            suggestion_key="suggestion-empty",
            workspace_key="mark",
            space_key="personal",
            relationship_key="anna-personal",
            kind=RelationshipSuggestionKind.SUMMARY,
            proposed_value="Summary",
            confidence_basis_points=5_000,
            evidence=(),
            model_revision="fixture-v1",
            state=RelationshipSuggestionState.PENDING,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )


def test_owner_acceptance_records_decision_without_applying_mutation() -> None:
    result = decide_relationship_suggestion(suggestion(), decision())

    assert result.suggestion.state is RelationshipSuggestionState.ACCEPTED
    assert result.suggestion.revision == 2
    assert result.mutation_allowed is False
    assert result.telegram_delivery_allowed is False
    assert result.requires_separate_owner_command is True


def test_owner_rejection_is_an_explicit_terminal_decision() -> None:
    result = decide_relationship_suggestion(
        suggestion(),
        decision(accepted=False),
    )

    assert result.suggestion.state is RelationshipSuggestionState.REJECTED
    assert result.mutation_allowed is False


def test_exact_decision_replay_is_idempotent() -> None:
    command = decision()
    first = decide_relationship_suggestion(suggestion(), command)
    replay = decide_relationship_suggestion(first.suggestion, command)

    assert replay == first


def test_cross_workspace_decision_fails_closed() -> None:
    with pytest.raises(
        RelationshipCrmDomainError,
        match="suggestion_scope_mismatch",
    ):
        decide_relationship_suggestion(
            suggestion(),
            decision(workspace="other"),
        )


def test_sensitive_evidence_and_proposed_value_are_hidden_from_repr() -> None:
    rendered = repr(suggestion())

    assert "opaque-source-sensitive" not in rendered
    assert "Follow up next week" not in rendered
