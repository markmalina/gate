"""Safety invariants for Alpha installation, invocation and delivery."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.agent_kernel import AgentResultKind
from app.application.agent_shadow import AgentShadowProposal
from app.application.alpha_group import (
    AgentSendAttempt,
    AgentSendAttemptState,
    AlphaInstallation,
    AlphaInstallationState,
    AlphaInvocationFacts,
    AlphaInvocationKind,
    AlphaPolicyReason,
    evaluate_alpha_invocation,
    issue_alpha_delivery_grant,
)
from app.application.canonical_messaging import DialogKind


def _installation() -> AlphaInstallation:
    discovered = AlphaInstallation(
        installation_key="installation-1",
        workspace_id="mark-personal",
        dialog_id="group-1",
        dialog_kind=DialogKind.SUPERGROUP,
        deployment_key="alpha-v1",
        instruction_content_sha256="b" * 64,
    )
    return discovered.request_approval().approve()


def _decision() -> object:
    return evaluate_alpha_invocation(
        AlphaInvocationFacts(
            connector_enabled=True,
            installation=_installation(),
            sender_is_human=True,
            invocation_kind=AlphaInvocationKind.COMMAND,
            turns_in_window=0,
            max_turns_per_window=5,
        )
    )


def _proposal() -> AgentShadowProposal:
    return AgentShadowProposal(
        turn_key="turn-1",
        deployment_key="alpha-v1",
        instruction_commit_sha="a" * 40,
        instruction_content_sha256="b" * 64,
        policy_version="alpha-v1",
        result_kind=AgentResultKind.REPLY,
        result_code="reply",
        candidate_text="Candidate",
        candidate_sha256="c" * 64,
    )


def test_unknown_group_never_skips_pending_approval() -> None:
    installation = replace(_installation(), state=AlphaInstallationState.DISCOVERED)
    decision = evaluate_alpha_invocation(
        AlphaInvocationFacts(
            connector_enabled=True,
            installation=installation,
            sender_is_human=True,
            invocation_kind=AlphaInvocationKind.MENTION,
            turns_in_window=0,
            max_turns_per_window=5,
        )
    )

    assert decision.allowed is False
    assert decision.reason is AlphaPolicyReason.INSTALLATION_INACTIVE


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"connector_enabled": False}, AlphaPolicyReason.CONNECTOR_DISABLED),
        ({"sender_is_human": False}, AlphaPolicyReason.SENDER_NOT_HUMAN),
        ({"invocation_kind": AlphaInvocationKind.NONE}, AlphaPolicyReason.NOT_INVOKED),
        ({"turns_in_window": 5}, AlphaPolicyReason.RATE_LIMITED),
    ],
)
def test_every_independent_policy_gate_fails_closed(
    change: dict[str, object], reason: AlphaPolicyReason
) -> None:
    facts = AlphaInvocationFacts(
        connector_enabled=True,
        installation=_installation(),
        sender_is_human=True,
        invocation_kind=AlphaInvocationKind.REPLY_TO_BOT,
        turns_in_window=0,
        max_turns_per_window=5,
    )

    decision = evaluate_alpha_invocation(replace(facts, **change))

    assert decision.allowed is False
    assert decision.reason is reason


def test_grant_is_exact_target_text_revision_and_one_use() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    decision = _decision()
    grant = issue_alpha_delivery_grant(
        grant_key="secret-grant",
        installation=_installation(),
        decision=decision,  # type: ignore[arg-type]
        proposal=_proposal(),
        trigger_message_id="message-1",
        now=now,
    )

    consumed = grant.consume(now=now + timedelta(seconds=1), candidate_sha256="c" * 64)
    assert consumed.used_at is not None
    assert "secret-grant" not in repr(grant)
    assert "group-1" not in repr(grant)
    with pytest.raises(ValueError, match="already_used"):
        consumed.consume(now=now + timedelta(seconds=2), candidate_sha256="c" * 64)
    with pytest.raises(ValueError, match="candidate_mismatch"):
        grant.consume(now=now + timedelta(seconds=2), candidate_sha256="d" * 64)


def test_unknown_send_outcome_is_terminal_and_not_retryable() -> None:
    attempt = AgentSendAttempt(attempt_key="attempt-1", grant_key="grant-1")
    unknown = attempt.begin().finish(AgentSendAttemptState.UNKNOWN)

    assert unknown.retryable is False
    with pytest.raises(ValueError, match="not_reserved"):
        unknown.begin()
