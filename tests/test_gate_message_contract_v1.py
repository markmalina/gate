"""Contract, privacy and fail-closed proofs for Gate message v1 preflight."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from app.application.gate_message_contract_v1 import (
    GATE_MESSAGE_V1_MAX_BYTES,
    DeliveryDisposition,
    GateMessageAdapterMode,
    GateMessageAdapterV1,
    GateMessageContractError,
    delivery_disposition_v1,
    parse_gate_message_json_v1,
    parse_gate_message_object_v1,
    preserve_legacy_result_on_shadow_failure,
    serialize_gate_message_json_v1,
)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "contracts" / "gate" / "v1"

CASES = (
    ("message-received.valid.json", "message-received.v1.schema.json"),
    ("message-send.valid.json", "message-send.v1.schema.json"),
    ("delivery-fact.committed.json", "delivery-fact.v1.schema.json"),
    ("delivery-fact.unknown.json", "delivery-fact.v1.schema.json"),
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fixture(name: str) -> dict[str, Any]:
    return _load(CONTRACT / "fixtures" / name)


def _schema(name: str) -> dict[str, Any]:
    return _load(CONTRACT / "schemas" / name)


@pytest.mark.parametrize(("fixture_name", "schema_name"), CASES)
def test_schema_and_python_parser_agree_and_round_trip(
    fixture_name: str, schema_name: str
) -> None:
    fixture, schema = _fixture(fixture_name), _schema(schema_name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture)
    document = parse_gate_message_object_v1(fixture)
    assert document.to_wire() == fixture
    assert parse_gate_message_json_v1(serialize_gate_message_json_v1(document)).to_wire() == fixture


def test_adapter_is_disabled_and_cannot_be_string_enabled() -> None:
    fixture = _fixture("message-received.valid.json")
    with pytest.raises(GateMessageContractError, match="ADAPTER_DISABLED"):
        GateMessageAdapterV1().rehearse(fixture)
    with pytest.raises(GateMessageContractError, match="MODE_INVALID"):
        GateMessageAdapterV1(mode="synthetic_rehearsal")  # type: ignore[arg-type]
    assert (
        GateMessageAdapterV1(GateMessageAdapterMode.SYNTHETIC_REHEARSAL)
        .rehearse(fixture)
        .to_wire()
        == fixture
    )


def test_unknown_missing_and_unsupported_fields_fail_closed() -> None:
    fixture = _fixture("message-received.valid.json")
    unknown = dict(fixture, provider_payload={})
    missing = deepcopy(fixture)
    missing.pop("destination_ref")
    unsupported = dict(fixture, schema_version="gate.message.received.v2")
    for candidate in (unknown, missing, unsupported):
        with pytest.raises(GateMessageContractError):
            parse_gate_message_object_v1(candidate)


def test_duplicate_property_nonfinite_malformed_and_oversized_fail_closed() -> None:
    duplicate = b'{"schema_version":"gate.message.received.v1","schema_version":"x"}'
    nonfinite = b'{"schema_version":NaN}'
    for raw in (duplicate, nonfinite, b"{", b"\xff", b"x" * (GATE_MESSAGE_V1_MAX_BYTES + 1)):
        with pytest.raises(GateMessageContractError):
            parse_gate_message_json_v1(raw)


def test_media_is_opaque_bounded_and_has_no_url_or_provider_payload() -> None:
    fixture = _fixture("message-send.valid.json")
    wire = parse_gate_message_object_v1(fixture).to_wire()
    assert set(wire["payload"]) == {
        "kind",
        "object_ref",
        "sha256",
        "size_bytes",
        "content_type",
    }
    for field, value in (
        ("size_bytes", 25 * 1024 * 1024 + 1),
        ("object_ref", "https://signed.invalid/object"),
        ("content_type", "https://provider.invalid"),
    ):
        candidate = deepcopy(fixture)
        candidate["payload"][field] = value
        with pytest.raises(GateMessageContractError):
            parse_gate_message_object_v1(candidate)


def test_route_mismatch_and_stale_generation_are_detectable() -> None:
    received = _fixture("message-received.valid.json")
    send = _fixture("message-send.valid.json")
    assert received["destination_ref"] == send["destination_ref"]
    assert received["route_generation"] == send["route_generation"]
    stale = deepcopy(received)
    stale["route_generation"] = 0
    with pytest.raises(GateMessageContractError, match="ROUTE_GENERATION_INVALID"):
        parse_gate_message_object_v1(stale)


def test_delivery_ordering_and_unknown_retry_policy() -> None:
    committed = _fixture("delivery-fact.committed.json")
    unknown = _fixture("delivery-fact.unknown.json")
    assert delivery_disposition_v1(committed) is DeliveryDisposition.SETTLED
    assert delivery_disposition_v1(unknown) is DeliveryDisposition.RECONCILE_ONLY

    missing_commit = dict(committed, consumer_commit_ref=None)
    with pytest.raises(GateMessageContractError):
        parse_gate_message_object_v1(missing_commit)
    blind_retry = dict(unknown, status="retryable")
    with pytest.raises(GateMessageContractError):
        parse_gate_message_object_v1(blind_retry)


def test_retry_is_bounded() -> None:
    fact = _fixture("delivery-fact.unknown.json")
    retryable = dict(fact, status="retryable", attempt=2, reconciliation_ref=None)
    assert delivery_disposition_v1(retryable) is DeliveryDisposition.RETRY
    retryable["attempt"] = 3
    assert delivery_disposition_v1(retryable) is DeliveryDisposition.SETTLED
    retryable["attempt"] = 4
    with pytest.raises(GateMessageContractError):
        delivery_disposition_v1(retryable)


def test_shadow_failure_cannot_block_legacy_result() -> None:
    def fail() -> None:
        raise RuntimeError("synthetic shadow failure")

    legacy = {"accepted": True, "reply": "synthetic legacy"}
    assert preserve_legacy_result_on_shadow_failure(legacy, fail) is legacy


def test_contract_is_product_neutral_and_runtime_unwired() -> None:
    inspected = list(CONTRACT.rglob("*"))
    inspected.extend(
        [
            ROOT / "app" / "application" / "gate_message_contract_v1.py",
            ROOT / "app" / "infrastructure" / "gate_message_preflight_store.py",
        ]
    )
    forbidden = (
        "aqub_food_",
        "general cube",
        "crm",
        "relations",
        "nutrition",
        "prompt",
        "transcript",
        "model policy",
        "telegram",
        "railway",
    )
    text = "\n".join(
        path.read_text(encoding="utf-8").lower() for path in inspected if path.is_file()
    )
    for token in forbidden:
        assert token not in text

    runtime_paths = [ROOT / "app" / "main.py", ROOT / "app" / "core" / "config.py"]
    runtime_paths.extend((ROOT / "app" / "api").rglob("*.py"))
    runtime_paths.extend((ROOT / "app" / "worker").rglob("*.py"))
    assert runtime_paths
    for path in runtime_paths:
        source = path.read_text(encoding="utf-8")
        assert "gate_message_contract_v1" not in source
        assert "gate_message_preflight_store" not in source
