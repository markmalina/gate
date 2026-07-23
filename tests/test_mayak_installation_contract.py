"""Repository contract tests for reusable Mayak installations."""

from __future__ import annotations

from copy import deepcopy
from json import loads
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

SCHEMA_PATH = (
    Path(__file__).parents[1] / "registry" / "mayak-installation.v1.schema.json"
)
GATE_REGISTRY_PATH = (
    Path(__file__).parents[1] / "registry" / "gate-capabilities.v1.json"
)


def _schema() -> dict[str, object]:
    return loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _capabilities(**enabled: bool) -> dict[str, bool]:
    result = {
        "receiveExplicitInvocations": False,
        "reply": False,
        "directMessaging": False,
        "publish": False,
        "manageMembers": False,
        "manageSettings": False,
    }
    result.update(enabled)
    return result


def _installation(*, state: str = "active") -> dict[str, object]:
    return {
        "contract": "gate.mayak-installation",
        "version": "1.0.0",
        "installationRef": "installation_01J000000INS",
        "idempotencyKey": "idempotency_01J0000000KEY",
        "scope": {
            "workspaceRef": "workspace_01J0000000WRK",
            "connectorRef": "connector_01J0000000CON",
            "dialogRef": "dialog_01J0000000DLG",
            "dialogKind": "group",
        },
        "agentIdentity": {
            "mayakRef": "mayak_01J0000000MYK",
            "publicLabel": "Synthetic Mayak",
            "controllerLabel": "Synthetic controller",
            "disclosureText": "Synthetic Mayak; automated assistance is visible.",
            "identityVisible": True,
            "actsAs": "mayak",
        },
        "basis": {
            "capabilityGrantRef": "capability_01J000000CAP",
            "consentBasisRef": "consent_01J0000000CNS",
            "purposeRef": "purpose_01J0000000PUR",
            "installationAllowed": True,
        },
        "state": state,
        "capabilities": _capabilities(
            receiveExplicitInvocations=state == "active",
            reply=state == "active",
        ),
        "retention": {
            "class": "consent-bound",
            "deleteOnConsentRevocation": True,
            "deleteOnWorkspaceDeletion": True,
        },
        "recordedAt": "2026-07-23T03:30:00Z",
        "deliveryAuthorized": False,
        "providerActionAuthorized": False,
        "publicationAuthorized": False,
        "lmsMutationAuthorized": False,
        "externalEffectsPerformed": False,
    }


def _receipt(kind: str = "personal-alfa-opt-in") -> dict[str, object]:
    return {
        "contract": "gate.mayak-installation-transition-receipt",
        "version": "1.0.0",
        "receiptRef": "receipt_01J0000000RCP",
        "installationRef": "installation_01J000000INS",
        "workspaceRef": "workspace_01J0000000WRK",
        "idempotencyKey": "idempotency_01J0000000TRN",
        "transitionKind": kind,
        "sourceState": "active",
        "targetState": "active",
        "targetRef": "alfa_01J0000000ALF",
        "explicitConsent": True,
        "identityTransitionExplicit": True,
        "userFacingDisclosureObserved": True,
        "state": "accepted",
        "reasonCode": None,
        "recordedAt": "2026-07-23T03:30:01Z",
        "deliveryAuthorized": False,
        "providerActionAuthorized": False,
        "publicationAuthorized": False,
        "lmsMutationAuthorized": False,
        "externalEffectsPerformed": False,
    }


def _validate(value: dict[str, object]) -> None:
    Draft202012Validator(
        _schema(),
        format_checker=FormatChecker(),
    ).validate(value)


def test_schema_and_synthetic_examples_are_valid() -> None:
    Draft202012Validator.check_schema(_schema())
    _validate(_installation())
    _validate(_receipt())


def test_gate_registry_points_to_the_p4_contract() -> None:
    registry = loads(GATE_REGISTRY_PATH.read_text(encoding="utf-8"))
    p4 = next(stage for stage in registry["roadmap"] if stage["id"] == "P4")
    capability = next(
        item
        for item in registry["capabilities"]
        if item["id"] == "mayak-installation"
    )

    assert p4 == {
        "id": "P4",
        "name": "reusable-mayak-installation",
        "state": "contract-ready-runtime-gated",
        "pointer": "https://github.com/markmalina/gate/issues/293",
    }
    assert capability["lifecycle"] == "contract-only"
    assert capability["currentPointer"] == p4["pointer"]
    assert "visible-agent-installation-contract" in capability["current"]
    assert "explicit-transition-receipts" in capability["current"]


@pytest.mark.parametrize("dialog_kind", ["private", "group", "community", "channel"])
def test_all_supported_dialog_kinds_are_explicit(dialog_kind: str) -> None:
    installation = _installation()
    scope = installation["scope"]
    assert isinstance(scope, dict)
    scope["dialogKind"] = dialog_kind
    _validate(installation)


@pytest.mark.parametrize(
    "state",
    ["discovered", "pending-approval", "paused", "revoked", "deleted"],
)
def test_non_active_installations_are_capability_off(state: str) -> None:
    _validate(_installation(state=state))

    installation = _installation(state=state)
    capabilities = installation["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["reply"] = True
    with pytest.raises(ValidationError):
        _validate(installation)


def test_capabilities_are_explicit_and_do_not_imply_channel_parity() -> None:
    installation = _installation()
    capabilities = installation["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities.pop("publish")
    with pytest.raises(ValidationError):
        _validate(installation)

    installation = _installation()
    capabilities = installation["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["unknownProviderCapability"] = True
    with pytest.raises(ValidationError):
        _validate(installation)


@pytest.mark.parametrize(
    "forbidden",
    [
        {"providerChatId": "-100"},
        {"providerUserId": "123"},
        {"memberList": ["person"]},
        {"rawHistory": ["synthetic message"]},
        {"promptDump": "synthetic transcript"},
        {"recipientRef": "provider_recipient"},
        {"accessToken": "synthetic-secret"},
        {"personRecord": {"name": "synthetic"}},
        {"lmsProgress": 42},
    ],
)
def test_privacy_and_canonical_domain_fields_fail_closed(
    forbidden: dict[str, object],
) -> None:
    installation = _installation()
    installation.update(forbidden)
    with pytest.raises(ValidationError):
        _validate(installation)


def test_cross_workspace_injection_has_no_representable_shape() -> None:
    installation = _installation()
    installation["targetWorkspaceRef"] = "workspace_01J0000000TWO"
    with pytest.raises(ValidationError):
        _validate(installation)

    receipt = _receipt()
    receipt["targetWorkspaceRef"] = "workspace_01J0000000TWO"
    with pytest.raises(ValidationError):
        _validate(receipt)


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("program-handoff", "explicitConsent"),
        ("personal-alfa-opt-in", "explicitConsent"),
        ("personal-alfa-opt-in", "identityTransitionExplicit"),
        ("human-takeover", "identityTransitionExplicit"),
    ],
)
def test_accepted_handoffs_require_explicit_visible_transition(
    kind: str,
    field: str,
) -> None:
    receipt = _receipt(kind)
    receipt[field] = False
    with pytest.raises(ValidationError):
        _validate(receipt)

    receipt = _receipt(kind)
    receipt["userFacingDisclosureObserved"] = False
    with pytest.raises(ValidationError):
        _validate(receipt)


def test_external_authority_is_always_false() -> None:
    for field in (
        "deliveryAuthorized",
        "providerActionAuthorized",
        "publicationAuthorized",
        "lmsMutationAuthorized",
        "externalEffectsPerformed",
    ):
        installation = _installation()
        installation[field] = True
        with pytest.raises(ValidationError):
            _validate(installation)

        receipt = _receipt()
        receipt[field] = True
        with pytest.raises(ValidationError):
            _validate(receipt)


def test_receipt_is_terminal_reference_evidence_without_payload() -> None:
    receipt = _receipt()
    _validate(receipt)

    replay = deepcopy(receipt)
    replay["state"] = "replayed"
    _validate(replay)

    reconciliation = deepcopy(receipt)
    reconciliation["state"] = "reconciliation-required"
    reconciliation["explicitConsent"] = False
    reconciliation["identityTransitionExplicit"] = False
    reconciliation["userFacingDisclosureObserved"] = False
    _validate(reconciliation)

    receipt["payload"] = {"message": "forbidden"}
    with pytest.raises(ValidationError):
        _validate(receipt)
