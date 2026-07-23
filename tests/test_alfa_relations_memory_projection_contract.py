"""Repository contract tests for Alfa/Relations memory projections."""

from __future__ import annotations

from copy import deepcopy
from json import loads
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

SCHEMA_PATH = (
    Path(__file__).parents[1]
    / "registry"
    / "alfa-relations-memory-projection.v1.schema.json"
)
GATE_REGISTRY_PATH = (
    Path(__file__).parents[1] / "registry" / "gate-capabilities.v1.json"
)


def _schema() -> dict[str, object]:
    return loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _projection() -> dict[str, object]:
    return {
        "contract": "gate.alfa-relations.memory-projection",
        "version": "1.0.0",
        "projectionRef": "projection_01J0000000PRJ",
        "idempotencyKey": "idempotency_01J0000000KEY",
        "scope": {
            "workspaceRef": "workspace_01J0000000WRK",
            "subjectRef": "subject_01J0000000SUB",
            "dialogRef": "dialog_01J0000000DLG",
        },
        "source": {
            "canonicalEventRef": "event_01J0000000EVT",
            "transport": "telegram-business",
            "occurredAt": "2026-07-23T03:20:00Z",
            "sourceDigestSha256": "1" * 64,
            "redactionState": "structured-only",
        },
        "basis": {
            "capabilityRef": "capability_01J000000CAP",
            "consentBasisRef": "consent_01J0000000CNS",
            "purposeRef": "purpose_01J0000000PUR",
            "projectionAllowed": True,
        },
        "operation": "upsert",
        "consumerTargets": ["alfa", "relations"],
        "facts": [
            {
                "factRef": "fact_01J0000000FCT",
                "kind": "commitment-candidate",
                "assertionStatus": "candidate",
                "value": "Synthetic commitment candidate.",
                "sensitivity": "standard",
            }
        ],
        "retention": {
            "class": "source-bound",
            "expiresAt": None,
            "deleteOnSourceDeletion": True,
        },
        "emittedAt": "2026-07-23T03:20:01Z",
        "deliveryAuthorized": False,
        "providerActionAuthorized": False,
        "externalEffectsPerformed": False,
    }


def _receipt() -> dict[str, object]:
    return {
        "contract": "gate.alfa-relations.memory-projection-receipt",
        "version": "1.0.0",
        "receiptRef": "receipt_01J0000000RCP",
        "projectionRef": "projection_01J0000000PRJ",
        "idempotencyKey": "idempotency_01J0000000KEY",
        "workspaceRef": "workspace_01J0000000WRK",
        "consumer": "relations",
        "state": "accepted",
        "reasonCode": None,
        "projectionDigestSha256": "2" * 64,
        "recordedAt": "2026-07-23T03:20:02Z",
        "deliveryAuthorized": False,
        "providerActionAuthorized": False,
        "externalEffectsPerformed": False,
    }


def _validate(value: dict[str, object]) -> None:
    Draft202012Validator(
        _schema(),
        format_checker=FormatChecker(),
    ).validate(value)


def test_schema_and_synthetic_contract_examples_are_valid() -> None:
    Draft202012Validator.check_schema(_schema())
    _validate(_projection())
    _validate(_receipt())


def test_gate_registry_points_to_the_p3_contract() -> None:
    registry = loads(GATE_REGISTRY_PATH.read_text(encoding="utf-8"))
    p3 = next(stage for stage in registry["roadmap"] if stage["id"] == "P3")
    capability = next(
        item
        for item in registry["capabilities"]
        if item["id"] == "alfa-relations-memory-projection"
    )

    assert p3 == {
        "id": "P3",
        "name": "alfa-relations-memory",
        "state": "contract-ready-runtime-gated",
        "pointer": "https://github.com/markmalina/gate/issues/291",
    }
    assert capability["lifecycle"] == "contract-only"
    assert capability["currentPointer"] == p3["pointer"]
    assert "source-attributed-projection-contract" in capability["current"]
    assert "terminal-digest-receipt" in capability["current"]


@pytest.mark.parametrize(
    "forbidden",
    [
        {"providerUserId": "123"},
        {"providerChatId": "-100"},
        {"providerMessageId": "456"},
        {"rawHistory": ["synthetic message"]},
        {"promptDump": "synthetic transcript"},
        {"recipientRef": "provider_recipient"},
        {"deliveryGrant": True},
        {"accessToken": "synthetic-secret"},
    ],
)
def test_privacy_and_external_authority_fields_fail_closed(
    forbidden: dict[str, object],
) -> None:
    projection = _projection()
    projection.update(forbidden)
    with pytest.raises(ValidationError):
        _validate(projection)


def test_cross_workspace_injection_has_no_representable_shape() -> None:
    projection = _projection()
    projection["targetWorkspaceRef"] = "workspace_01J0000000TWO"
    with pytest.raises(ValidationError):
        _validate(projection)

    fact = projection["facts"][0]
    assert isinstance(fact, dict)
    fact["workspaceRef"] = "workspace_01J0000000TWO"
    projection.pop("targetWorkspaceRef")
    with pytest.raises(ValidationError):
        _validate(projection)


def test_basis_is_default_deny_and_external_authority_is_always_false() -> None:
    for path in (
        ("basis", "projectionAllowed"),
        (None, "deliveryAuthorized"),
        (None, "providerActionAuthorized"),
        (None, "externalEffectsPerformed"),
    ):
        projection = _projection()
        parent = projection if path[0] is None else projection[path[0]]
        assert isinstance(parent, dict)
        parent[path[1]] = not parent[path[1]]
        with pytest.raises(ValidationError):
            _validate(projection)


def test_text_is_bounded_and_full_history_is_not_representable() -> None:
    projection = _projection()
    fact = projection["facts"][0]
    assert isinstance(fact, dict)
    fact["value"] = "x" * 1025
    with pytest.raises(ValidationError):
        _validate(projection)

    projection = _projection()
    fact = projection["facts"][0]
    assert isinstance(fact, dict)
    fact["value"] = ["message one", "message two"]
    with pytest.raises(ValidationError):
        _validate(projection)


@pytest.mark.parametrize("operation", ["correct", "supersede", "delete", "retain"])
def test_lineage_operations_require_an_opaque_target(operation: str) -> None:
    projection = _projection()
    projection["operation"] = operation
    if operation in {"delete", "retain"}:
        projection["facts"] = []
    with pytest.raises(ValidationError):
        _validate(projection)

    projection["targetProjectionRef"] = "projection_01J0000000OLD"
    _validate(projection)


def test_terminal_receipt_has_no_payload_and_is_immutable_by_digest() -> None:
    receipt = _receipt()
    _validate(receipt)

    mutated = deepcopy(receipt)
    mutated["payload"] = {"fact": "forbidden"}
    with pytest.raises(ValidationError):
        _validate(mutated)

    mutated = deepcopy(receipt)
    mutated["state"] = "unknown"
    with pytest.raises(ValidationError):
        _validate(mutated)
