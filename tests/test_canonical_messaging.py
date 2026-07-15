"""Contract tests for workspace-scoped canonical messaging."""

from dataclasses import fields

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    CanonicalMessageEnvelope,
    ConnectorKind,
    DialogIdentity,
    DialogKind,
    WorkOwnershipDecision,
    WorkRoute,
    WorkspaceRef,
)
from app.application.work_ownership import AgentKey, WorkOwnershipState


def test_dialog_identity_is_workspace_scoped_and_project_independent() -> None:
    identity = DialogIdentity(
        workspace_id="mark-personal",
        connector_key="telegram_business",
        connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
        provider_dialog_id="private-provider-dialog",
        dialog_kind=DialogKind.PRIVATE,
    )

    assert identity.workspace_id == "mark-personal"
    assert "project" not in {item.name for item in fields(identity)}
    assert "private-provider-dialog" not in repr(identity)


def test_message_repr_excludes_provider_identities() -> None:
    message = CanonicalMessageEnvelope(
        workspace_id="mark-personal",
        connector_key="telegram_business",
        source_receipt_id="private-receipt",
        provider_dialog_id="private-dialog",
        provider_message_id="private-message",
        provider_sender_id="private-sender",
        direction=CanonicalMessageDirection.INBOUND,
        text_present=True,
    )

    wire = repr(message)
    assert "private-dialog" not in wire
    assert "private-message" not in wire
    assert "private-sender" not in wire


def test_agent_route_requires_authoritative_agent_state_and_identity() -> None:
    decision = WorkOwnershipDecision(
        state=WorkOwnershipState.AGENT_MANAGED,
        route=WorkRoute.AGENT_TURN,
        policy_version="ownership-v1",
        candidate_agent=AgentKey.ALPHA_GROUP,
        reason_code="approved_installation",
    )

    assert decision.candidate_agent is AgentKey.ALPHA_GROUP

    with pytest.raises(ValueError, match="agent_turn requires"):
        WorkOwnershipDecision(
            state=WorkOwnershipState.HUMAN_MANAGED,
            route=WorkRoute.AGENT_TURN,
            policy_version="ownership-v1",
        )


def test_human_route_rejects_agent_managed_state() -> None:
    with pytest.raises(ValueError, match="agent_managed state"):
        WorkOwnershipDecision(
            state=WorkOwnershipState.AGENT_MANAGED,
            route=WorkRoute.HUMAN_REPLY_TASK,
            policy_version="ownership-v1",
            candidate_agent=AgentKey.OKO,
        )


@pytest.mark.parametrize("value", ("", " padded", "padded ", "x" * 129))
def test_workspace_ref_rejects_invalid_tokens(value: str) -> None:
    with pytest.raises(ValueError, match="workspace_id"):
        WorkspaceRef(value)
