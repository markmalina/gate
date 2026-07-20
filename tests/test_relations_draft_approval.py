from __future__ import annotations

from copy import deepcopy

import pytest
from app.application.relations_draft_approval import (
    ACCEPTANCE_CONTRACT,
    RELATIONS_MAIN_SHA,
    RelationsApprovalAcceptanceError,
    RelationsApprovalAcceptancePolicy,
    RelationsApprovalReplayLedger,
    accept_relations_draft_approval,
)


def _event() -> dict[str, object]:
    return {
        "contract": "relations.draft-approval-recorded",
        "version": "1.0.0",
        "canonicalStatus": "proposed-local",
        "eventRef": "event_01J0000000EVT",
        "idempotencyKey": "idempotency_01J0000000KEY",
        "occurredAt": "2026-07-20T21:00:00Z",
        "payload": {
            "draftRef": "draft_01J0000000DRF",
            "draftVersion": 3,
            "personRef": "person_01J0000000ONE",
            "contentRef": "content_01J0000000CNT",
            "contentDigestSha256": "1" * 64,
            "decision": "approved",
            "decidedByRef": "person_01J0000000ACT",
            "evidenceRef": "evidence_01J000000APR",
            "tenantRef": "tenant_01J0000000TEN",
            "purposeRef": "purpose_01J0000000REL",
            "visibility": "family",
            "deliveryAuthorized": False,
        },
        "externalEffectsPerformed": False,
    }


def _accept(event: dict[str, object] | None = None) -> dict[str, object]:
    return accept_relations_draft_approval(
        event or _event(),
        policy=RelationsApprovalAcceptancePolicy(enabled=True),
    )


def test_runtime_is_default_off() -> None:
    with pytest.raises(RelationsApprovalAcceptanceError, match="disabled"):
        accept_relations_draft_approval(_event())


def test_acceptance_pins_source_and_never_authorizes_delivery() -> None:
    result = _accept()
    assert result["contract"] == ACCEPTANCE_CONTRACT
    assert result["sourceCommit"] == RELATIONS_MAIN_SHA
    assert result["acceptedFor"] == "reviewed-draft-reference-only"
    assert result["deliveryAuthorized"] is False
    assert result["providerRef"] is None
    assert result["deliveryRef"] is None
    assert result["externalEffectsPerformed"] is False
    assert "message" not in repr(result).lower()


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda event: event.update({"messageText": "forbidden"}), "event_shape"),
        (lambda event: event["payload"].update({"providerRef": "provider_x"}), "payload_shape"),
        (lambda event: event["payload"].update({"deliveryAuthorized": True}), "authority"),
        (lambda event: event["payload"].update({"draftVersion": 0}), "version"),
        (lambda event: event["payload"].update({"contentDigestSha256": "bad"}), "digest"),
        (lambda event: event.update({"canonicalStatus": "canonical"}), "proposed_local"),
        (lambda event: event.update({"externalEffectsPerformed": True}), "external_effect"),
    ],
)
def test_forbidden_or_mutated_shapes_fail_closed(mutator, reason: str) -> None:
    event = _event()
    mutator(event)
    with pytest.raises(RelationsApprovalAcceptanceError, match=reason):
        _accept(event)


def test_contract_and_opaque_references_fail_closed() -> None:
    event = _event()
    event["version"] = "2.0.0"
    with pytest.raises(RelationsApprovalAcceptanceError, match="contract_mismatch"):
        _accept(event)

    event = _event()
    event["payload"]["personRef"] = "123456"
    with pytest.raises(RelationsApprovalAcceptanceError, match="reference_invalid"):
        _accept(event)


def test_identical_replay_is_accepted_and_mutation_rejected() -> None:
    event = _event()
    result = _accept(event)
    ledger = RelationsApprovalReplayLedger()
    key = str(event["idempotencyKey"])
    assert ledger.record(key, result) == "recorded"
    assert ledger.record(key, deepcopy(result)) == "replayed"
    changed = dict(result)
    changed["approvalDecision"] = "rejected"
    with pytest.raises(RelationsApprovalAcceptanceError, match="replay_mutation"):
        ledger.record(key, changed)
