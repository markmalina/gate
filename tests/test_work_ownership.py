"""Pure contract tests for non-authoritative work ownership."""

import pytest
from app.application.work_ownership import (
    AgentKey,
    BusinessScope,
    DirectionEvidence,
    OwnershipRequirement,
    OwnershipSignal,
    ShadowWorkOwnershipEvaluation,
    WorkOwnershipFacts,
    WorkOwnershipReason,
    WorkOwnershipState,
    evaluate_shadow_work_ownership,
)


def test_default_work_remains_human_managed() -> None:
    result = evaluate_shadow_work_ownership(WorkOwnershipFacts())

    assert result == ShadowWorkOwnershipEvaluation(
        proposed_state=WorkOwnershipState.HUMAN_MANAGED,
        candidate_agent=None,
        reason=WorkOwnershipReason.DEFAULT_HUMAN,
        unresolved_requirements=(),
    )
    assert result.authoritative is False


@pytest.mark.parametrize(
    ("signal", "scope", "agent", "reason"),
    [
        (
            OwnershipSignal.ALPHA_GROUP_INVOCATION,
            BusinessScope.NOT_APPLICABLE,
            AgentKey.ALPHA_GROUP,
            WorkOwnershipReason.ALPHA_GROUP_INVOCATION_CANDIDATE,
        ),
        (
            OwnershipSignal.OKO_EXACT_ENROLLMENT,
            BusinessScope.RU,
            AgentKey.OKO,
            WorkOwnershipReason.OKO_EXACT_ENROLLMENT_CANDIDATE,
        ),
    ],
)
def test_agent_signals_are_candidates_with_human_fallback(
    signal: OwnershipSignal,
    scope: BusinessScope,
    agent: AgentKey,
    reason: WorkOwnershipReason,
) -> None:
    result = evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=signal,
            business_scope=scope,
            direction_evidence=DirectionEvidence.HUMAN_INBOUND,
        )
    )

    assert result.proposed_state is WorkOwnershipState.HUMAN_MANAGED
    assert result.candidate_agent is agent
    assert result.reason is reason
    assert result.unresolved_requirements
    assert result.authoritative is False


def test_by_oko_signal_never_becomes_an_agent_candidate() -> None:
    result = evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=OwnershipSignal.OKO_EXACT_ENROLLMENT,
            business_scope=BusinessScope.BY,
        )
    )

    assert result.proposed_state is WorkOwnershipState.HUMAN_MANAGED
    assert result.candidate_agent is None
    assert result.reason is WorkOwnershipReason.BY_DEFERRED


def test_unresolved_routing_quarantines_before_candidate_evaluation() -> None:
    result = evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=OwnershipSignal.OKO_EXACT_ENROLLMENT,
            business_scope=BusinessScope.UNKNOWN,
            routing_resolved=False,
        )
    )

    assert result.proposed_state is WorkOwnershipState.QUARANTINED
    assert result.candidate_agent is None
    assert result.reason is WorkOwnershipReason.UNRESOLVED_CONTEXT
    assert result.unresolved_requirements == (
        OwnershipRequirement.WORKSPACE,
        OwnershipRequirement.CONNECTOR_ROLE,
        OwnershipRequirement.DIALOG,
    )


@pytest.mark.parametrize(
    "state",
    [
        WorkOwnershipState.AGENT_MANAGED,
        WorkOwnershipState.HANDOFF_REQUIRED,
        WorkOwnershipState.PAUSED,
    ],
)
def test_shadow_contract_rejects_authoritative_agent_states(
    state: WorkOwnershipState,
) -> None:
    with pytest.raises(ValueError, match="cannot activate"):
        ShadowWorkOwnershipEvaluation(
            proposed_state=state,
            candidate_agent=None,
            reason=WorkOwnershipReason.UNRESOLVED_CONTEXT,
            unresolved_requirements=(),
        )


def test_shadow_contract_rejects_candidate_without_human_fallback_checks() -> None:
    with pytest.raises(ValueError, match="human fallback"):
        ShadowWorkOwnershipEvaluation(
            proposed_state=WorkOwnershipState.IGNORED,
            candidate_agent=AgentKey.OKO,
            reason=WorkOwnershipReason.OKO_EXACT_ENROLLMENT_CANDIDATE,
            unresolved_requirements=(),
        )


def test_log_fields_are_stable_and_payload_free() -> None:
    result = evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=OwnershipSignal.ALPHA_GROUP_INVOCATION,
            business_scope=BusinessScope.NOT_APPLICABLE,
            direction_evidence=DirectionEvidence.HUMAN_INBOUND,
        )
    )

    assert result.log_fields() == {
        "proposed_state": "human_managed",
        "candidate_agent": "alpha_group",
        "reason": "alpha_group_invocation_candidate",
        "observed_signal": "alpha_group_invocation",
        "unresolved_requirements": [
            "workspace",
            "connector_role",
            "dialog",
            "installation",
            "deployment",
        ],
        "policy_version": "work-ownership-shadow-v1",
        "authoritative": False,
    }


def test_oko_signal_without_direction_evidence_cannot_become_a_candidate() -> None:
    result = evaluate_shadow_work_ownership(
        WorkOwnershipFacts(
            signal=OwnershipSignal.OKO_EXACT_ENROLLMENT,
            business_scope=BusinessScope.RU,
        )
    )

    assert result.proposed_state is WorkOwnershipState.HUMAN_MANAGED
    assert result.candidate_agent is None
    assert result.observed_signal is OwnershipSignal.OKO_EXACT_ENROLLMENT
    assert result.reason is WorkOwnershipReason.UNRESOLVED_CONTEXT
    assert result.unresolved_requirements == (OwnershipRequirement.DIRECTION,)
