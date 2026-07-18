"""Compatibility guards for the accepted AlphaVia Gate boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).parents[1] / "contracts" / "alphavia"
V1 = ROOT / "v1"
CONTRACTS = (
    "gate-inbound-intent",
    "gate-delivery-grant",
    "gate-delivery-fact",
)
FORBIDDEN_CROSS_DOMAIN_KEYS = {
    "telegram_id",
    "chat_id",
    "provider_id",
    "provider_message_id",
    "raw_message",
    "raw_event",
    "message_text",
    "phone",
    "email",
    "access_token",
    "secret",
    "token",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


@pytest.mark.parametrize("name", CONTRACTS)
def test_canonical_fixture_validates_against_pinned_schema(name: str) -> None:
    schema = _load(V1 / "schemas" / f"{name}.schema.json")
    fixture = _load(V1 / "fixtures" / f"{name}.example.json")

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixture)
    assert not (_keys(fixture) & FORBIDDEN_CROSS_DOMAIN_KEYS)


def test_inbound_contract_exposes_only_opaque_refs_and_intent_metadata() -> None:
    inbound = _load(V1 / "fixtures" / "gate-inbound-intent.example.json")

    assert set(inbound["payload"]) == {
        "channel_ref",
        "person_ref",
        "intent",
        "consent_ref",
        "purpose",
    }
    assert "telegram" not in json.dumps(inbound).lower()


def test_delivery_grant_fixture_is_blocked_and_has_no_delivery_authority() -> None:
    manifest = _load(ROOT / "manifest.json")
    grant = _load(V1 / "fixtures" / "gate-delivery-grant.example.json")
    grant_metadata = next(
        item for item in manifest["contracts"] if item["id"] == "gate.delivery-grant"
    )

    assert manifest["externalEffectsAuthorized"] is False
    assert manifest["runtimeWiring"] is False
    assert manifest["durableSharedReplayRequiredBeforeActivation"] is True
    assert grant_metadata["status"] == "proposed-blocked"
    assert grant_metadata["owner"] == "Delivery Authority"
    assert grant["payload"]["authorized"] is False


def test_unknown_delivery_fact_is_terminal_reconciliation_evidence() -> None:
    fact = _load(V1 / "fixtures" / "gate-delivery-fact.example.json")

    assert fact["payload"]["outcome"] == "unknown"
    assert fact["payload"]["reconciliation_required"] is True
    assert "retry" not in fact["payload"]


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_CROSS_DOMAIN_KEYS))
def test_schemas_reject_provider_and_raw_fields(forbidden: str) -> None:
    schema = _load(V1 / "schemas" / "gate-inbound-intent.schema.json")
    fixture = _load(V1 / "fixtures" / "gate-inbound-intent.example.json")
    fixture["payload"][forbidden] = "must-not-cross"

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(fixture)
