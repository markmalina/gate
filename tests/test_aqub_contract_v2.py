"""Parity and safety tests for the production-inactive AQUB v2 adapter."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from app.application.aqub_contract_v2 import (
    AQUB_V2_MAX_JSON_BYTES,
    AQUBContractV2Adapter,
    AQUBContractV2Error,
    AQUBV2AdapterMode,
    AQUBV2DeliveryAction,
    AQUBV2Document,
    assert_delivery_correlation_v2,
    assert_route_match_v2,
    delivery_disposition_v2,
    map_opaque_binding_evidence_v2,
    parse_contract_json_v2,
    parse_delivery_ack_v2,
    parse_delivery_command_v2,
    parse_delivery_receipt_v2,
    parse_transport_envelope_v2,
    serialize_contract_json_v2,
)
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "aqub" / "v2"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"

Parser = Callable[[Mapping[str, Any]], AQUBV2Document]

CASES: tuple[tuple[str, str, Parser], ...] = (
    (
        "transport-envelope.valid.json",
        "transport-envelope.v2.schema.json",
        parse_transport_envelope_v2,
    ),
    (
        "delivery-command.valid.json",
        "delivery-command.v2.schema.json",
        parse_delivery_command_v2,
    ),
    (
        "delivery-ack.committed.json",
        "delivery-ack.v2.schema.json",
        parse_delivery_ack_v2,
    ),
    (
        "delivery-receipt.sent.json",
        "delivery-receipt.v2.schema.json",
        parse_delivery_receipt_v2,
    ),
    (
        "delivery-receipt.failed.json",
        "delivery-receipt.v2.schema.json",
        parse_delivery_receipt_v2,
    ),
    (
        "delivery-receipt.unknown.json",
        "delivery-receipt.v2.schema.json",
        parse_delivery_receipt_v2,
    ),
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fixture(name: str) -> dict[str, Any]:
    return _load(FIXTURE_ROOT / name)


def _schema(name: str) -> dict[str, Any]:
    return _load(SCHEMA_ROOT / name)


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        _schema(name),
        format_checker=FormatChecker(),
    )


def _route_evidence() -> dict[str, Any]:
    command = _fixture("delivery-command.valid.json")
    return {
        "binding_ref": "binding:opaque_binding_01",
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
    }


def _git_blob_sha(path: Path) -> str:
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _collect_keys(value: object, output: set[str] | None = None) -> set[str]:
    result = output if output is not None else set()
    if isinstance(value, dict):
        for key, nested in value.items():
            result.add(key)
            _collect_keys(nested, result)
    elif isinstance(value, list):
        for nested in value:
            _collect_keys(nested, result)
    return result


def test_provenance_pins_exact_accepted_aqub_artifacts() -> None:
    provenance = _load(CONTRACT_ROOT / "provenance.json")

    assert provenance["schema_version"] == "gate.aqub-contract-v2-provenance.v1"
    assert provenance["upstream"] == {
        "repository": "markmalina/aqub",
        "pull_request": 43,
        "accepted_head": "9f22609a905a8e12bf886376fb9d486699aa818a",
        "merge_commit": "cadd5f0ff9731b1114e85e95023ddb3f5d5099ba",
    }
    assert provenance["runtime_wiring"] is True
    assert provenance["runtime_default_enabled"] is False
    assert provenance["route_activation"] is False
    assert provenance["external_effects_authorized"] is False

    artifacts = provenance["mirrored_artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) == 10
    overrides = {
        value["local_path"]: value for value in provenance["candidate_overrides"]
    }
    assert overrides == {
        "contracts/aqub/v2/schemas/delivery-command.v2.schema.json": {
            "local_path": "contracts/aqub/v2/schemas/delivery-command.v2.schema.json",
            "base_upstream_blob_sha": "bb7e5d45d0209084f6407a30934499427f644cbb",
            "candidate_blob_sha": "72f308f3c7d4ad361a625d9c487dcc648f4be1e3",
            "rationale": (
                "Work B adds the mandatory closed Food delivery_type enum; "
                "this is not attributed to accepted AQUB PR 43."
            ),
        },
        "contracts/aqub/v2/fixtures/delivery-command.valid.json": {
            "local_path": "contracts/aqub/v2/fixtures/delivery-command.valid.json",
            "base_upstream_blob_sha": "130202ce3f8b98cf29fa2897d01cf348658fecb2",
            "candidate_blob_sha": "bcd5309dd0bd10ca4cd296cdca5c3c117ec1e04d",
            "rationale": (
                "Work B fixture exercises the Gate candidate extension and "
                "requires an exact paired AQUB head."
            ),
        },
    }
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        local_path = ROOT / artifact["local_path"]
        assert local_path.is_file()
        override = overrides.get(artifact["local_path"])
        expected = (
            override["candidate_blob_sha"]
            if override is not None
            else artifact["upstream_blob_sha"]
        )
        if override is not None:
            assert override["base_upstream_blob_sha"] == artifact["upstream_blob_sha"]
        assert _git_blob_sha(local_path) == expected

    assert provenance["candidate_pairing"] == {
        "repository": "AlphaCenter/aqub",
        "pull_request": 175,
        "exact_head": "f68d408a1a4e8cc2394a2843c8d657d9794c2d87",
        "status": "CANDIDATE_EXACT_HEAD_PINNED",
        "artifacts": [
            {
                "upstream_path": "contracts/delivery-command.v2.schema.json",
                "local_path": (
                    "contracts/aqub/v2/schemas/delivery-command.v2.schema.json"
                ),
                "upstream_blob_sha": "dd37523bb8e4ffd51c5b1e06a298364836878b2e",
                "local_blob_sha": "72f308f3c7d4ad361a625d9c487dcc648f4be1e3",
                "comparison": "JSON_VALUE_IDENTICAL",
            },
            {
                "upstream_path": (
                    "contracts/fixtures/v2/delivery-command.valid.json"
                ),
                "local_path": (
                    "contracts/aqub/v2/fixtures/delivery-command.valid.json"
                ),
                "upstream_blob_sha": "bcd5309dd0bd10ca4cd296cdca5c3c117ec1e04d",
                "local_blob_sha": "bcd5309dd0bd10ca4cd296cdca5c3c117ec1e04d",
                "comparison": "BYTE_IDENTICAL",
            },
        ],
    }

    for artifact in provenance["candidate_pairing"]["artifacts"]:
        local_path = ROOT / artifact["local_path"]
        assert _git_blob_sha(local_path) == artifact["local_blob_sha"]

    assert provenance["source_references"] == [
        {
            "path": "src/gate-adapter/contracts-v2.js",
            "blob_sha": "2135d7ec1527d45424d1453d061ff61a34b99fec",
        },
        {
            "path": "docs/decisions/0002-aqub-gate-ownership-and-contract-v2.md",
            "blob_sha": "ad51c4bec7aaeea4f1253026a2334f9fc6318d83",
        },
    ]


@pytest.mark.parametrize(("fixture_name", "schema_name", "parser"), CASES)
def test_positive_fixtures_round_trip_exactly(
    fixture_name: str,
    schema_name: str,
    parser: Parser,
) -> None:
    fixture = _fixture(fixture_name)
    schema = _schema(schema_name)
    Draft202012Validator.check_schema(schema)
    _validator(schema_name).validate(fixture)

    parsed = parser(fixture)

    assert parsed.to_wire() == fixture
    detached = parsed.to_wire()
    detached["schema_version"] = "mutated"
    assert parsed.to_wire() == fixture

    encoded_once = serialize_contract_json_v2(parsed)
    encoded_twice = serialize_contract_json_v2(parsed)
    assert encoded_once == encoded_twice
    assert parse_contract_json_v2(encoded_once).to_wire() == fixture


@pytest.mark.parametrize(("fixture_name", "schema_name", "parser"), CASES[:4])
def test_root_required_keys_match_schema_and_python(
    fixture_name: str,
    schema_name: str,
    parser: Parser,
) -> None:
    fixture = _fixture(fixture_name)
    schema = _schema(schema_name)
    required = schema["required"]
    assert isinstance(required, list)

    for key in required:
        candidate = deepcopy(fixture)
        candidate.pop(key)
        with pytest.raises(ValidationError):
            _validator(schema_name).validate(candidate)
        with pytest.raises(AQUBContractV2Error):
            parser(candidate)


@pytest.mark.parametrize(
    ("fixture_name", "schema_name", "parser", "nested_key"),
    [
        (
            "transport-envelope.valid.json",
            "transport-envelope.v2.schema.json",
            parse_transport_envelope_v2,
            "content",
        ),
        (
            "delivery-command.valid.json",
            "delivery-command.v2.schema.json",
            parse_delivery_command_v2,
            "payload",
        ),
    ],
)
def test_nested_required_keys_match_schema_and_python(
    fixture_name: str,
    schema_name: str,
    parser: Parser,
    nested_key: str,
) -> None:
    fixture = _fixture(fixture_name)
    schema = _schema(schema_name)
    required = schema["properties"][nested_key]["required"]
    assert isinstance(required, list)
    for key in required:
        candidate = deepcopy(fixture)
        nested = candidate[nested_key]
        assert isinstance(nested, dict)
        nested.pop(key)
        with pytest.raises(ValidationError):
            _validator(schema_name).validate(candidate)
        with pytest.raises(AQUBContractV2Error):
            parser(candidate)


def test_adapter_is_default_disabled_and_only_reviewed_boundary_is_wired() -> None:
    envelope = _fixture("transport-envelope.valid.json")
    evidence = _route_evidence()

    with pytest.raises(AQUBContractV2Error, match="adapter_disabled"):
        AQUBContractV2Adapter().rehearse_ingress(envelope, evidence)
    with pytest.raises(AQUBContractV2Error, match="mode_invalid"):
        AQUBContractV2Adapter(mode="synthetic_rehearsal")  # type: ignore[arg-type]
    with pytest.raises(AQUBContractV2Error, match="mode_invalid"):
        AQUBContractV2Adapter(mode=True)  # type: ignore[arg-type]

    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    config_source = (ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    boundary_source = (
        ROOT / "app" / "api" / "aqub_delivery_v2_internal.py"
    ).read_text(encoding="utf-8")
    assert "aqub_delivery_v2_internal_router" in main_source
    assert "app.application.aqub_delivery_v2 import" not in main_source
    assert "default=False" in config_source
    assert "GATE_AQUB_V2_DELIVERY_ENDPOINT_ENABLED" in config_source
    assert "AQUBV2DeliverySQLStore" in boundary_source
    assert "AQUBV2DeliveryExecutor" not in boundary_source
    assert "httpx" not in boundary_source
    for path in (ROOT / "app" / "worker").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "AQUBContractV2Adapter" not in source, path
        assert "AQUBV2DeliveryExecutor" not in source, path


def test_byte_parser_rejects_duplicates_nonfinite_values_and_non_object_roots() -> None:
    duplicate = (
        b'{"schema_version":"aqub.transport-envelope.v2",'
        b'"schema_version":"aqub.delivery-command.v2"}'
    )
    with pytest.raises(AQUBContractV2Error) as duplicate_error:
        parse_contract_json_v2(duplicate)
    assert duplicate_error.value.code == "AQUB_V2_DUPLICATE_PROPERTY"
    assert "schema_version" not in str(duplicate_error.value)

    for payload in (
        b'{"schema_version":"aqub.transport-envelope.v2","value":NaN}',
        b'{"schema_version":"aqub.transport-envelope.v2","value":Infinity}',
        b"[]",
        b"null",
        b'"aqub.transport-envelope.v2"',
        b"\xff",
        b"[" * 1_100 + b"]" * 1_100,
        b'{"route_generation":' + b"9" * 5_000 + b"}",
    ):
        with pytest.raises(AQUBContractV2Error):
            parse_contract_json_v2(payload)


def test_byte_parser_has_predecode_ceiling_with_escaped_text_headroom() -> None:
    maximum = _fixture("delivery-command.valid.json")
    maximum["payload"]["text"] = "😀" * 32_768
    escaped = json.dumps(
        maximum,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")

    assert len(escaped) < AQUB_V2_MAX_JSON_BYTES
    assert parse_contract_json_v2(escaped).to_wire() == maximum

    exact_ceiling = escaped + b" " * (AQUB_V2_MAX_JSON_BYTES - len(escaped))
    assert len(exact_ceiling) == AQUB_V2_MAX_JSON_BYTES
    assert parse_contract_json_v2(exact_ceiling).to_wire() == maximum

    with pytest.raises(AQUBContractV2Error) as too_large:
        parse_contract_json_v2(exact_ceiling + b" ")
    assert too_large.value.code == "AQUB_V2_JSON_TOO_LARGE"


@pytest.mark.parametrize(
    "text",
    [
        "\ud800",
        "\udfff",
        "😀",
        "Привет, AQUB — 你好",
        "Привет, AQUB — 你好 😀 \ud800 \udfff",
    ],
)
def test_unicode_matches_aqub_js_and_ascii_serialization_is_safe(text: str) -> None:
    command = _fixture("delivery-command.valid.json")
    command["payload"]["text"] = text
    payload = json.dumps(command, ensure_ascii=True).encode("ascii")

    _validator("delivery-command.v2.schema.json").validate(command)
    parsed = parse_contract_json_v2(payload)
    serialized = serialize_contract_json_v2(parsed)

    assert serialized.isascii()
    assert parse_contract_json_v2(serialized).to_wire()["payload"]["text"] == text


@pytest.mark.parametrize(
    ("fixture_name", "parser"),
    [
        ("delivery-ack.committed.json", parse_delivery_ack_v2),
        ("delivery-receipt.sent.json", parse_delivery_receipt_v2),
        ("delivery-receipt.failed.json", parse_delivery_receipt_v2),
        ("delivery-receipt.unknown.json", parse_delivery_receipt_v2),
    ],
)
@pytest.mark.parametrize("malformed_status", [{}, []])
def test_malformed_status_has_controlled_contract_error(
    fixture_name: str,
    parser: Parser,
    malformed_status: object,
) -> None:
    candidate = _fixture(fixture_name)
    candidate["status"] = malformed_status

    with pytest.raises(ValidationError):
        schema_name = (
            "delivery-ack.v2.schema.json"
            if "ack" in fixture_name
            else "delivery-receipt.v2.schema.json"
        )
        _validator(schema_name).validate(candidate)
    with pytest.raises(AQUBContractV2Error):
        parser(candidate)
    with pytest.raises(AQUBContractV2Error):
        parse_contract_json_v2(json.dumps(candidate).encode("utf-8"))


def test_route_generation_rejects_bool_and_serializer_never_normalizes() -> None:
    envelope = _fixture("transport-envelope.valid.json")
    envelope["route_generation"] = True
    with pytest.raises(ValidationError):
        _validator("transport-envelope.v2.schema.json").validate(envelope)
    with pytest.raises(AQUBContractV2Error, match="ROUTE_GENERATION"):
        parse_transport_envelope_v2(envelope)

    for field_name, invalid_value in (
        ("occurred_at", "2026-08-10T12:00:60.000Z"),
        ("actor_ref", "actor:12345678"),
    ):
        document = parse_transport_envelope_v2(
            _fixture("transport-envelope.valid.json")
        )
        invalid_wire = document.to_wire()
        invalid_wire[field_name] = invalid_value
        object.__setattr__(
            document,
            "_canonical_json",
            json.dumps(
                invalid_wire,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        with pytest.raises(AQUBContractV2Error):
            serialize_contract_json_v2(document)


@pytest.mark.parametrize("numeric_spelling", ["3.0", "3e0"])
def test_integral_json_route_generation_matches_schema_and_aqub_js(
    numeric_spelling: str,
) -> None:
    command = _fixture("delivery-command.valid.json")
    command["route_generation"] = 3.0
    _validator("delivery-command.v2.schema.json").validate(command)
    assert parse_delivery_command_v2(command).to_wire()["route_generation"] == 3

    canonical = json.dumps(
        _fixture("delivery-command.valid.json"),
        separators=(",", ":"),
    ).encode()
    wire = canonical.replace(
        b'"route_generation":3',
        f'"route_generation":{numeric_spelling}'.encode(),
    )
    parsed = parse_contract_json_v2(wire)
    serialized = serialize_contract_json_v2(parsed)

    assert type(parsed.to_wire()["route_generation"]) is int
    assert b'"route_generation":3' in serialized
    assert f'"route_generation":{numeric_spelling}'.encode() not in serialized
    assert map_opaque_binding_evidence_v2(
        {**_route_evidence(), "route_generation": 3.0}
    ).route_generation == 3


@pytest.mark.parametrize(
    "invalid_generation",
    [True, 1.5, float("nan"), float("inf"), float("-inf"), 0, 2_147_483_648],
)
def test_route_generation_rejects_nonintegral_nonfinite_bool_and_bounds(
    invalid_generation: object,
) -> None:
    command = _fixture("delivery-command.valid.json")
    command["route_generation"] = invalid_generation
    with pytest.raises(AQUBContractV2Error, match="ROUTE_GENERATION"):
        parse_delivery_command_v2(command)


def test_document_construction_immutability_and_public_revalidation() -> None:
    with pytest.raises(AQUBContractV2Error, match="CONSTRUCTION_FORBIDDEN"):
        AQUBV2Document(
            schema_version="aqub.transport-envelope.v2",
            _canonical_json=b"{}",
        )

    envelope = parse_transport_envelope_v2(_fixture("transport-envelope.valid.json"))
    with pytest.raises(FrozenInstanceError):
        envelope._canonical_json = b"{}"  # type: ignore[misc]

    forged_wire = envelope.to_wire()
    forged_wire["actor_ref"] = "actor:12345678"
    object.__setattr__(
        envelope,
        "_canonical_json",
        json.dumps(forged_wire, sort_keys=True, separators=(",", ":")).encode(),
    )
    with pytest.raises(AQUBContractV2Error):
        assert_route_match_v2(
            envelope,
            map_opaque_binding_evidence_v2(_route_evidence()),
        )

    command = parse_delivery_command_v2(_fixture("delivery-command.valid.json"))
    ack = parse_delivery_ack_v2(_fixture("delivery-ack.committed.json"))
    forged_ack = ack.to_wire()
    forged_ack["status"] = []
    object.__setattr__(
        ack,
        "_canonical_json",
        json.dumps(forged_ack, sort_keys=True, separators=(",", ":")).encode(),
    )
    with pytest.raises(AQUBContractV2Error):
        assert_delivery_correlation_v2(command, ack)

    receipt = parse_delivery_receipt_v2(
        _fixture("delivery-receipt.unknown.json")
    )
    forged_receipt = receipt.to_wire()
    forged_receipt["retry_disposition"] = "do_not_retry"
    object.__setattr__(
        receipt,
        "_canonical_json",
        json.dumps(
            forged_receipt,
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    )
    with pytest.raises(AQUBContractV2Error):
        delivery_disposition_v2(receipt)


def test_explicit_synthetic_seam_maps_only_opaque_route_evidence() -> None:
    envelope = _fixture("transport-envelope.valid.json")
    evidence = _route_evidence()
    adapter = AQUBContractV2Adapter(
        mode=AQUBV2AdapterMode.SYNTHETIC_REHEARSAL
    )

    assert adapter.rehearse_ingress(envelope, evidence).to_wire() == envelope
    route = map_opaque_binding_evidence_v2(evidence)
    assert route.installation_ref == envelope["installation_ref"]
    assert route.cube_ref == envelope["cube_ref"]
    assert route.route_generation == envelope["route_generation"]

    raw = deepcopy(evidence)
    raw["telegram_chat_id"] = -100123456
    with pytest.raises(AQUBContractV2Error, match="EXTRA_PROPERTY"):
        map_opaque_binding_evidence_v2(raw)

    stale = deepcopy(evidence)
    stale["route_generation"] -= 1
    with pytest.raises(AQUBContractV2Error, match="ROUTE_MISMATCH"):
        adapter.rehearse_ingress(envelope, stale)


def test_delivery_correlation_preserves_all_route_and_idempotency_fences() -> None:
    command_wire = _fixture("delivery-command.valid.json")
    ack_wire = _fixture("delivery-ack.committed.json")
    adapter = AQUBContractV2Adapter(
        mode=AQUBV2AdapterMode.SYNTHETIC_REHEARSAL
    )
    adapter.rehearse_delivery(command_wire, ack_wire, _route_evidence())

    command = parse_delivery_command_v2(command_wire)
    for key, replacement in (
        ("command_id", "22222222-2222-4222-8222-222222222222"),
        ("idempotency_key", "delivery/test/other"),
        ("installation_ref", "installation:opaque_installation_02"),
        ("cube_ref", "cube:opaque_cube_02"),
        ("route_generation", 2),
    ):
        mismatch = deepcopy(ack_wire)
        mismatch[key] = replacement
        parsed = parse_delivery_ack_v2(mismatch)
        with pytest.raises(AQUBContractV2Error, match="CORRELATION_MISMATCH"):
            assert_delivery_correlation_v2(command, parsed)


def test_unknown_is_reconcile_only_and_never_collapses_to_retry() -> None:
    unknown = _fixture("delivery-receipt.unknown.json")
    sent = parse_delivery_receipt_v2(_fixture("delivery-receipt.sent.json"))
    failed = parse_delivery_receipt_v2(_fixture("delivery-receipt.failed.json"))
    parsed_unknown = parse_delivery_receipt_v2(unknown)

    assert delivery_disposition_v2(parsed_unknown) is AQUBV2DeliveryAction.RECONCILE
    assert delivery_disposition_v2(sent) is AQUBV2DeliveryAction.SETTLED
    assert delivery_disposition_v2(failed) is AQUBV2DeliveryAction.SETTLED
    assert parsed_unknown.to_wire()["reconciliation_ref"] == unknown[
        "reconciliation_ref"
    ]

    retry = deepcopy(unknown)
    retry["retry_disposition"] = "do_not_retry"
    with pytest.raises(AQUBContractV2Error, match="REQUIRES_RECONCILIATION"):
        parse_delivery_receipt_v2(retry)

    collapsed = deepcopy(unknown)
    collapsed["status"] = "failed"
    with pytest.raises(AQUBContractV2Error):
        parse_delivery_receipt_v2(collapsed)

    blind_retry = deepcopy(unknown)
    blind_retry["automatic_retry"] = True
    with pytest.raises(AQUBContractV2Error, match="EXTRA_PROPERTY"):
        parse_delivery_receipt_v2(blind_retry)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-10 12:00:00.000Z",
        "2026-08-10T15:00:00.000+03:00",
        "2026-02-30T12:00:00.000Z",
        "2026-08-10T12:00:00Z",
        "2026-08-10T12:00:60.000Z",
    ],
)
def test_timestamp_parity_rejects_noncanonical_and_leap_second(
    timestamp: str,
) -> None:
    envelope = _fixture("transport-envelope.valid.json")
    envelope["occurred_at"] = timestamp
    if timestamp != "2026-02-30T12:00:00.000Z":
        with pytest.raises(ValidationError):
            _validator("transport-envelope.v2.schema.json").validate(envelope)
    with pytest.raises(AQUBContractV2Error):
        parse_transport_envelope_v2(envelope)


@pytest.mark.parametrize(
    "timestamp",
    [
        "0000-01-01T00:00:00.000Z",
        "0000-02-29T23:59:59.999Z",
        "0004-02-29T12:00:00.000Z",
        "2000-02-29T12:00:00.000Z",
    ],
)
def test_proleptic_gregorian_year_zero_matches_schema_and_aqub_js(
    timestamp: str,
) -> None:
    envelope = _fixture("transport-envelope.valid.json")
    envelope["occurred_at"] = timestamp

    _validator("transport-envelope.v2.schema.json").validate(envelope)
    parsed = parse_transport_envelope_v2(envelope)
    assert parsed.to_wire()["occurred_at"] == timestamp
    assert parse_contract_json_v2(serialize_contract_json_v2(parsed)).to_wire()[
        "occurred_at"
    ] == timestamp


@pytest.mark.parametrize(
    "timestamp",
    [
        "0000-02-30T12:00:00.000Z",
        "0001-02-29T12:00:00.000Z",
        "1900-02-29T12:00:00.000Z",
        "2000-02-30T12:00:00.000Z",
        "0000-04-31T12:00:00.000Z",
        "0000-00-01T12:00:00.000Z",
        "0000-13-01T12:00:00.000Z",
        "0000-01-00T12:00:00.000Z",
        "0000-01-01T24:00:00.000Z",
        "0000-01-01T23:60:00.000Z",
    ],
)
def test_proleptic_gregorian_invalid_calendar_values_fail_closed(
    timestamp: str,
) -> None:
    envelope = _fixture("transport-envelope.valid.json")
    envelope["occurred_at"] = timestamp

    # The mirrored schema carries a date-time format annotation; the accepted
    # AQUB validator and Gate both apply the explicit semantic calendar fence.
    with pytest.raises(AQUBContractV2Error, match="DATETIME"):
        parse_transport_envelope_v2(envelope)


@pytest.mark.parametrize(
    "timestamp",
    [
        "٢٠٢٦-08-10T12:00:00.000Z",
        "2026-٠٨-10T12:00:00.000Z",
        "2026-08-١٠T12:00:00.000Z",
        "2026-08-10T١٢:00:00.000Z",
        "2026-08-10T12:٠٠:00.000Z",
        "2026-08-10T12:00:٠٠.000Z",
        "2026-08-10T12:00:00.٠٠٠Z",
        "2٠26-0٨-1٠T1٢:0٠:00.0٠0Z",
    ],
)
def test_non_ascii_timestamp_digits_fail_schema_aqub_and_gate_parity(
    timestamp: str,
) -> None:
    envelope = _fixture("transport-envelope.valid.json")
    envelope["occurred_at"] = timestamp
    schema = _schema("transport-envelope.v2.schema.json")
    pattern = schema["$defs"]["timestamp"]["pattern"]

    assert isinstance(pattern, str)
    # JSON Schema patterns use ECMA-262 semantics, where \d is ASCII-only.
    assert re.fullmatch(pattern, timestamp, flags=re.ASCII) is None
    with pytest.raises(AQUBContractV2Error) as object_error:
        parse_transport_envelope_v2(envelope)
    assert object_error.value.code == "AQUB_V2_BAD_STRING:occurred_at"
    with pytest.raises(AQUBContractV2Error) as byte_error:
        parse_contract_json_v2(
            json.dumps(envelope, ensure_ascii=True).encode("ascii")
        )
    assert byte_error.value.code == "AQUB_V2_BAD_STRING:occurred_at"


def test_uuid_version_and_variant_parity() -> None:
    valid = _fixture("delivery-command.valid.json")
    valid["command_id"] = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
    _validator("delivery-command.v2.schema.json").validate(valid)
    assert parse_delivery_command_v2(valid).to_wire() == valid

    for invalid_uuid in (
        "11111111-1111-7111-8111-111111111111",
        "11111111-1111-4111-7111-111111111111",
    ):
        invalid = _fixture("delivery-command.valid.json")
        invalid["command_id"] = invalid_uuid
        with pytest.raises(ValidationError):
            _validator("delivery-command.v2.schema.json").validate(invalid)
        with pytest.raises(AQUBContractV2Error):
            parse_delivery_command_v2(invalid)


@pytest.mark.parametrize(
    "delivery_type",
    (
        "food.clarification",
        "food.fact_reply",
        "food.correction_reply",
        "food.day_summary",
        "food.omark_check",
    ),
)
def test_food_delivery_type_closed_enum_parity(delivery_type: str) -> None:
    command = _fixture("delivery-command.valid.json")
    command["payload"]["delivery_type"] = delivery_type
    _validator("delivery-command.v2.schema.json").validate(command)
    assert parse_delivery_command_v2(command).to_wire() == command


def test_unreviewed_food_delivery_type_fails_schema_and_python() -> None:
    command = _fixture("delivery-command.valid.json")
    command["payload"]["delivery_type"] = "food.unreviewed"
    with pytest.raises(ValidationError):
        _validator("delivery-command.v2.schema.json").validate(command)
    with pytest.raises(AQUBContractV2Error) as error:
        parse_delivery_command_v2(command)
    assert error.value.code == "AQUB_V2_BAD_DELIVERY_TYPE"


def test_unicode_text_length_counts_code_points_like_json_schema() -> None:
    maximum = _fixture("transport-envelope.valid.json")
    maximum["content"]["text"] = "😀" * 32_768
    assert len(maximum["content"]["text"]) == 32_768
    _validator("transport-envelope.v2.schema.json").validate(maximum)
    assert parse_transport_envelope_v2(maximum).to_wire() == maximum

    too_long = deepcopy(maximum)
    too_long["content"]["text"] += "😀"
    with pytest.raises(ValidationError):
        _validator("transport-envelope.v2.schema.json").validate(too_long)
    with pytest.raises(AQUBContractV2Error):
        parse_transport_envelope_v2(too_long)


def test_opaque_reference_minimum_and_maximum_match_schema() -> None:
    schema_name = "transport-envelope.v2.schema.json"
    minimum = _fixture("transport-envelope.valid.json")
    minimum["actor_ref"] = "actor:A1234567"
    _validator(schema_name).validate(minimum)
    parse_transport_envelope_v2(minimum)

    maximum = _fixture("transport-envelope.valid.json")
    maximum["installation_ref"] = f"installation:A{'b' * 127}"
    assert len(maximum["installation_ref"]) == 141
    _validator(schema_name).validate(maximum)
    parse_transport_envelope_v2(maximum)

    for field_name, value in (
        ("actor_ref", "actor:A123456"),
        ("installation_ref", f"installation:A{'b' * 128}"),
    ):
        invalid = _fixture("transport-envelope.valid.json")
        invalid[field_name] = value
        with pytest.raises(ValidationError):
            _validator(schema_name).validate(invalid)
        with pytest.raises(AQUBContractV2Error):
            parse_transport_envelope_v2(invalid)


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64])
def test_payload_digest_shape_fails_closed_in_schema_and_python(digest: str) -> None:
    envelope = _fixture("transport-envelope.valid.json")
    envelope["payload_sha256"] = digest
    with pytest.raises(ValidationError):
        _validator("transport-envelope.v2.schema.json").validate(envelope)
    with pytest.raises(AQUBContractV2Error):
        parse_transport_envelope_v2(envelope)


def test_conditional_ack_and_outcome_fields_match_schemas() -> None:
    rejected = _fixture("delivery-ack.committed.json")
    rejected["status"] = "rejected"
    with pytest.raises(ValidationError):
        _validator("delivery-ack.v2.schema.json").validate(rejected)
    with pytest.raises(AQUBContractV2Error):
        parse_delivery_ack_v2(rejected)

    rejected["reason_code"] = "ROUTE_STALE"
    _validator("delivery-ack.v2.schema.json").validate(rejected)
    parse_delivery_ack_v2(rejected)

    for fixture_name, forbidden_field in (
        ("delivery-receipt.sent.json", "failure_code"),
        ("delivery-receipt.failed.json", "reconciliation_ref"),
        ("delivery-receipt.unknown.json", "delivered_message_ref"),
    ):
        receipt = _fixture(fixture_name)
        receipt[forbidden_field] = "message:opaque_conflicting_fact_01"
        with pytest.raises(ValidationError):
            _validator("delivery-receipt.v2.schema.json").validate(receipt)
        with pytest.raises(AQUBContractV2Error):
            parse_delivery_receipt_v2(receipt)


def test_extra_product_provider_and_raw_identifier_fields_are_unrepresentable() -> None:
    forbidden = {
        "role",
        "program_ref",
        "progress",
        "assignment_ref",
        "step_ref",
        "model_policy",
        "prompt",
        "telegram_chat_id",
        "telegram_user_id",
        "provider_payload",
        "bot_token",
        "secret",
    }
    values = [
        _schema(schema_name)
        for schema_name in (
            "transport-envelope.v2.schema.json",
            "delivery-command.v2.schema.json",
            "delivery-ack.v2.schema.json",
            "delivery-receipt.v2.schema.json",
        )
    ]
    values.extend(_fixture(fixture_name) for fixture_name, _, _ in CASES)
    assert not (_collect_keys(values) & forbidden)

    envelope = _fixture("transport-envelope.valid.json")
    envelope["telegram_chat_id"] = -100123456
    with pytest.raises(ValidationError):
        _validator("transport-envelope.v2.schema.json").validate(envelope)
    with pytest.raises(AQUBContractV2Error, match="EXTRA_PROPERTY"):
        parse_transport_envelope_v2(envelope)

    raw_ref = _fixture("transport-envelope.valid.json")
    raw_ref["actor_ref"] = "actor:12345678"
    with pytest.raises(ValidationError):
        _validator("transport-envelope.v2.schema.json").validate(raw_ref)
    with pytest.raises(AQUBContractV2Error):
        parse_transport_envelope_v2(raw_ref)
