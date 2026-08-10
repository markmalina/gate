"""Disabled-by-default Gate adapter for the neutral AQUB contract v2.

The module is intentionally pure: it has no configuration, router, worker,
database, provider, or Telegram dependency.  It validates only transport
shape, opaque routing evidence, idempotency correlation, and delivery facts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Never

JsonObject = dict[str, Any]

# A 32,768-code-point text can occupy 393,216 bytes when every supplementary
# character is represented as a JSON surrogate pair (12 ASCII bytes). 512 KiB
# leaves more than 128 KiB for the bounded envelope fields and JSON syntax while
# placing a deterministic ceiling on allocation before UTF-8 decoding.
AQUB_V2_MAX_JSON_BYTES = 512 * 1024

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:~/-]{7,255}$")
_REFERENCE_SUFFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._~-]{7,127}$")
_MEDIA_TYPE_RE = re.compile(
    r"^[a-z][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_CANONICAL_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:[0-5][0-9]\.\d{3}Z$"
)
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")

_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "envelope_ref",
        "installation_ref",
        "cube_ref",
        "route_generation",
        "event_ref",
        "occurred_at",
        "conversation_ref",
        "actor_ref",
        "message_ref",
        "reply_to_message_ref",
        "transport_receipt_ref",
        "payload_sha256",
        "content",
    }
)
_CONTENT_KEYS = frozenset({"kind", "media_type", "text"})
_COMMAND_KEYS = frozenset(
    {
        "schema_version",
        "command_id",
        "idempotency_key",
        "installation_ref",
        "cube_ref",
        "route_generation",
        "conversation_ref",
        "requested_at",
        "payload",
    }
)
_PAYLOAD_KEYS = frozenset(
    {"kind", "media_type", "text", "reply_to_message_ref"}
)
_ACK_KEYS = frozenset(
    {
        "schema_version",
        "ack_ref",
        "command_id",
        "idempotency_key",
        "installation_ref",
        "cube_ref",
        "route_generation",
        "observed_at",
        "status",
        "reason_code",
    }
)
_ACK_BASE_REQUIRED = _ACK_KEYS - {"reason_code"}
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "receipt_ref",
        "command_id",
        "idempotency_key",
        "installation_ref",
        "cube_ref",
        "route_generation",
        "observed_at",
        "status",
        "retry_disposition",
        "transport_receipt_ref",
        "delivered_message_ref",
        "failure_code",
        "reconciliation_ref",
    }
)
_RECEIPT_REQUIRED = _RECEIPT_KEYS - {
    "delivered_message_ref",
    "failure_code",
    "reconciliation_ref",
}
_BINDING_EVIDENCE_KEYS = frozenset(
    {"binding_ref", "installation_ref", "cube_ref", "route_generation"}
)


class AQUBContractV2Error(RuntimeError):
    """A fail-closed refusal from the AQUB v2 transport boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AQUBV2AdapterMode(StrEnum):
    """Only an explicit code-owned synthetic mode can leave the disabled state."""

    DISABLED = "disabled"
    SYNTHETIC_REHEARSAL = "synthetic_rehearsal"


class AQUBV2DeliveryAction(StrEnum):
    SETTLED = "settled"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True, repr=False, init=False)
class AQUBV2Document:
    """Validated document backed by private immutable canonical JSON bytes.

    Construction is restricted to the validators in this module. Public
    semantic operations revalidate the bytes as an additional forged-object
    fence before trusting the schema or routing fields.
    """

    _schema_version: str = field(repr=False)
    _canonical_json: bytes = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise AQUBContractV2Error("AQUB_V2_DOCUMENT_CONSTRUCTION_FORBIDDEN")

    @property
    def schema_version(self) -> str:
        return self._schema_version

    def to_wire(self) -> JsonObject:
        """Revalidate and return a detached object with the accepted fields."""

        try:
            value = _decode_contract_json_bytes(self._canonical_json)
            validated = parse_contract_object_v2(value)
            if validated.schema_version != self._schema_version:
                _fail("AQUB_V2_DOCUMENT_INVALID")
        except (AttributeError, AQUBContractV2Error) as exc:
            raise AQUBContractV2Error("AQUB_V2_DOCUMENT_INVALID") from exc
        return value


@dataclass(frozen=True, slots=True)
class AQUBV2Route:
    installation_ref: str
    cube_ref: str
    route_generation: int

    def __post_init__(self) -> None:
        _assert_opaque_ref(self.installation_ref, "installation", "installation_ref")
        _assert_opaque_ref(self.cube_ref, "cube", "cube_ref")
        _assert_route_generation(self.route_generation)


@dataclass(frozen=True, slots=True)
class AQUBContractV2Adapter:
    """Production-inactive seam for later synthetic/shadow contract rehearsals.

    There is deliberately no boolean or string activation path and no runtime
    mode.  A caller must hold the enum member defined in this module, while the
    current application composition imports no reference to this adapter.
    """

    mode: AQUBV2AdapterMode = AQUBV2AdapterMode.DISABLED

    def __post_init__(self) -> None:
        if type(self.mode) is not AQUBV2AdapterMode:
            raise AQUBContractV2Error("aqub_v2_adapter_mode_invalid")

    def rehearse_ingress(
        self,
        envelope: Mapping[str, Any],
        binding_evidence: Mapping[str, Any],
    ) -> AQUBV2Document:
        self._require_synthetic_rehearsal()
        document = parse_transport_envelope_v2(envelope)
        route = map_opaque_binding_evidence_v2(binding_evidence)
        return assert_route_match_v2(document, route)

    def rehearse_delivery(
        self,
        command: Mapping[str, Any],
        fact: Mapping[str, Any],
        binding_evidence: Mapping[str, Any],
    ) -> tuple[AQUBV2Document, AQUBV2Document]:
        self._require_synthetic_rehearsal()
        parsed_command = parse_delivery_command_v2(command)
        parsed_fact = parse_delivery_fact_v2(fact)
        route = map_opaque_binding_evidence_v2(binding_evidence)
        assert_route_match_v2(parsed_command, route)
        assert_route_match_v2(parsed_fact, route)
        assert_delivery_correlation_v2(parsed_command, parsed_fact)
        return parsed_command, parsed_fact

    def _require_synthetic_rehearsal(self) -> None:
        if self.mode is not AQUBV2AdapterMode.SYNTHETIC_REHEARSAL:
            raise AQUBContractV2Error("aqub_v2_adapter_disabled")


def parse_transport_envelope_v2(value: Mapping[str, Any]) -> AQUBV2Document:
    envelope = _object(value, "envelope")
    _assert_exact_keys(envelope, _ENVELOPE_KEYS, _ENVELOPE_KEYS, "envelope")
    _assert_schema(envelope, "aqub.transport-envelope.v2")
    _assert_opaque_ref(envelope["envelope_ref"], "envelope", "envelope_ref")
    _assert_routing(envelope)
    _assert_opaque_ref(envelope["event_ref"], "event", "event_ref")
    _assert_datetime(envelope["occurred_at"], "occurred_at")
    _assert_opaque_ref(
        envelope["conversation_ref"], "conversation", "conversation_ref"
    )
    _assert_opaque_ref(envelope["actor_ref"], "actor", "actor_ref")
    _assert_opaque_ref(envelope["message_ref"], "message", "message_ref")
    _assert_nullable_opaque_ref(
        envelope["reply_to_message_ref"], "message", "reply_to_message_ref"
    )
    _assert_opaque_ref(
        envelope["transport_receipt_ref"], "receipt", "transport_receipt_ref"
    )
    _assert_string(
        envelope["payload_sha256"],
        "payload_sha256",
        minimum=64,
        maximum=64,
        pattern=_SHA256_RE,
    )
    _assert_text_content(envelope["content"], "content")
    return _document(envelope)


def parse_delivery_command_v2(value: Mapping[str, Any]) -> AQUBV2Document:
    command = _object(value, "command")
    _assert_exact_keys(command, _COMMAND_KEYS, _COMMAND_KEYS, "command")
    _assert_schema(command, "aqub.delivery-command.v2")
    _assert_uuid(command["command_id"], "command_id")
    _assert_idempotency_key(command["idempotency_key"])
    _assert_routing(command)
    _assert_opaque_ref(
        command["conversation_ref"], "conversation", "conversation_ref"
    )
    _assert_datetime(command["requested_at"], "requested_at")
    _assert_delivery_payload(command["payload"])
    return _document(command)


def parse_delivery_ack_v2(value: Mapping[str, Any]) -> AQUBV2Document:
    ack = _object(value, "ack")
    required = _ACK_KEYS if ack.get("status") == "rejected" else _ACK_BASE_REQUIRED
    _assert_exact_keys(ack, _ACK_KEYS, required, "ack")
    _assert_schema(ack, "aqub.delivery-ack.v2")
    _assert_opaque_ref(ack["ack_ref"], "ack", "ack_ref")
    _assert_uuid(ack["command_id"], "command_id")
    _assert_idempotency_key(ack["idempotency_key"])
    _assert_routing(ack)
    _assert_datetime(ack["observed_at"], "observed_at")
    status = ack["status"]
    if not isinstance(status, str) or status not in {
        "committed",
        "duplicate",
        "rejected",
    }:
        _fail("AQUB_V2_BAD_ACK_STATUS")
    if status == "rejected":
        _assert_reason_code(ack["reason_code"])
    elif "reason_code" in ack:
        _fail("AQUB_V2_UNEXPECTED_REASON")
    return _document(ack)


def parse_delivery_receipt_v2(value: Mapping[str, Any]) -> AQUBV2Document:
    receipt = _object(value, "receipt")
    _assert_exact_keys(receipt, _RECEIPT_KEYS, _RECEIPT_REQUIRED, "receipt")
    _assert_schema(receipt, "aqub.delivery-receipt.v2")
    _assert_opaque_ref(receipt["receipt_ref"], "receipt", "receipt_ref")
    _assert_uuid(receipt["command_id"], "command_id")
    _assert_idempotency_key(receipt["idempotency_key"])
    _assert_routing(receipt)
    _assert_datetime(receipt["observed_at"], "observed_at")
    _assert_opaque_ref(
        receipt["transport_receipt_ref"], "receipt", "transport_receipt_ref"
    )

    status = receipt["status"]
    if not isinstance(status, str) or status not in {"sent", "failed", "unknown"}:
        _fail("AQUB_V2_BAD_DELIVERY_STATUS")
    if status == "sent":
        _assert_retry_disposition(receipt, "do_not_retry")
        _assert_opaque_ref(
            receipt.get("delivered_message_ref"), "message", "delivered_message_ref"
        )
        _assert_absent(receipt, {"failure_code", "reconciliation_ref"})
    elif status == "failed":
        _assert_retry_disposition(receipt, "do_not_retry")
        _assert_reason_code(receipt.get("failure_code"), "failure_code")
        _assert_absent(receipt, {"delivered_message_ref", "reconciliation_ref"})
    else:
        if receipt["retry_disposition"] != "reconcile_only":
            _fail("AQUB_V2_UNKNOWN_REQUIRES_RECONCILIATION")
        _assert_opaque_ref(
            receipt.get("reconciliation_ref"),
            "reconcile",
            "reconciliation_ref",
        )
        _assert_absent(receipt, {"delivered_message_ref", "failure_code"})
    return _document(receipt)


def parse_delivery_fact_v2(value: Mapping[str, Any]) -> AQUBV2Document:
    schema_version = value.get("schema_version") if isinstance(value, Mapping) else None
    if schema_version == "aqub.delivery-ack.v2":
        return parse_delivery_ack_v2(value)
    if schema_version == "aqub.delivery-receipt.v2":
        return parse_delivery_receipt_v2(value)
    _fail("AQUB_V2_BAD_SCHEMA")


def parse_contract_json_v2(payload: bytes) -> AQUBV2Document:
    """Decode one strict UTF-8 JSON object and validate its v2 schema.

    Python's default JSON decoder accepts duplicate object keys and non-finite
    numeric constants.  Both are rejected here before contract validation so
    two implementations cannot interpret the same bytes differently.
    """

    value = _decode_contract_json_bytes(payload)
    return parse_contract_object_v2(value)


def parse_contract_object_v2(value: Mapping[str, Any]) -> AQUBV2Document:
    if not isinstance(value, Mapping):
        _fail("AQUB_V2_BAD_OBJECT")
    schema_version = value.get("schema_version")
    if schema_version == "aqub.transport-envelope.v2":
        return parse_transport_envelope_v2(value)
    if schema_version == "aqub.delivery-command.v2":
        return parse_delivery_command_v2(value)
    if schema_version == "aqub.delivery-ack.v2":
        return parse_delivery_ack_v2(value)
    if schema_version == "aqub.delivery-receipt.v2":
        return parse_delivery_receipt_v2(value)
    _fail("AQUB_V2_BAD_SCHEMA")


def serialize_contract_json_v2(document: AQUBV2Document) -> bytes:
    """Return deterministic UTF-8 JSON after revalidating the exact wire value."""

    validated = _revalidate_document(document)
    return validated._canonical_json


def map_opaque_binding_evidence_v2(value: Mapping[str, Any]) -> AQUBV2Route:
    """Map already-opaque Gate evidence into the three-field AQUB route scope."""

    evidence = _object(value, "binding_evidence")
    _assert_exact_keys(
        evidence,
        _BINDING_EVIDENCE_KEYS,
        _BINDING_EVIDENCE_KEYS,
        "binding_evidence",
    )
    _assert_opaque_ref(evidence["binding_ref"], "binding", "binding_ref")
    return AQUBV2Route(
        installation_ref=_string_value(evidence["installation_ref"]),
        cube_ref=_string_value(evidence["cube_ref"]),
        route_generation=_integer_value(evidence["route_generation"]),
    )


def assert_route_match_v2(
    document: AQUBV2Document, expected: AQUBV2Route
) -> AQUBV2Document:
    validated = _revalidate_document(document)
    if type(expected) is not AQUBV2Route:
        _fail("AQUB_V2_ROUTE_EVIDENCE_INVALID")
    try:
        expected = AQUBV2Route(
            installation_ref=expected.installation_ref,
            cube_ref=expected.cube_ref,
            route_generation=expected.route_generation,
        )
    except (AttributeError, AQUBContractV2Error) as exc:
        raise AQUBContractV2Error("AQUB_V2_ROUTE_EVIDENCE_INVALID") from exc
    wire = validated.to_wire()
    if (
        wire.get("installation_ref") != expected.installation_ref
        or wire.get("cube_ref") != expected.cube_ref
        or wire.get("route_generation") != expected.route_generation
    ):
        _fail("AQUB_V2_ROUTE_MISMATCH")
    return validated


def assert_delivery_correlation_v2(
    command: AQUBV2Document, fact: AQUBV2Document
) -> AQUBV2Document:
    validated_command = _revalidate_document(command)
    validated_fact = _revalidate_document(fact)
    if validated_command.schema_version != "aqub.delivery-command.v2":
        _fail("AQUB_V2_BAD_SCHEMA")
    if validated_fact.schema_version not in {
        "aqub.delivery-ack.v2",
        "aqub.delivery-receipt.v2",
    }:
        _fail("AQUB_V2_BAD_SCHEMA")
    command_wire = validated_command.to_wire()
    fact_wire = validated_fact.to_wire()
    for key in (
        "command_id",
        "idempotency_key",
        "installation_ref",
        "cube_ref",
        "route_generation",
    ):
        if fact_wire.get(key) != command_wire.get(key):
            _fail("AQUB_V2_DELIVERY_CORRELATION_MISMATCH")
    return validated_fact


def delivery_disposition_v2(receipt: AQUBV2Document) -> AQUBV2DeliveryAction:
    parsed = _revalidate_document(receipt)
    if parsed.schema_version != "aqub.delivery-receipt.v2":
        _fail("AQUB_V2_BAD_SCHEMA")
    if parsed.to_wire()["status"] == "unknown":
        return AQUBV2DeliveryAction.RECONCILE
    return AQUBV2DeliveryAction.SETTLED


def _document(value: Mapping[str, Any]) -> AQUBV2Document:
    canonical_json = _canonical_json_bytes(value)
    document = object.__new__(AQUBV2Document)
    object.__setattr__(
        document,
        "_schema_version",
        _string_value(value["schema_version"]),
    )
    object.__setattr__(document, "_canonical_json", canonical_json)
    return document


def _revalidate_document(document: AQUBV2Document) -> AQUBV2Document:
    if type(document) is not AQUBV2Document:
        _fail("AQUB_V2_DOCUMENT_INVALID")
    try:
        wire = document.to_wire()
        schema_version = document.schema_version
    except (AttributeError, AQUBContractV2Error) as exc:
        raise AQUBContractV2Error("AQUB_V2_DOCUMENT_INVALID") from exc
    validated = parse_contract_object_v2(wire)
    if validated.schema_version != schema_version:
        _fail("AQUB_V2_DOCUMENT_INVALID")
    return validated


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise AQUBContractV2Error(f"AQUB_V2_BAD_OBJECT:{label}")
    return dict(value)


def _assert_exact_keys(
    value: Mapping[str, Any],
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    keys = frozenset(value)
    if keys - allowed:
        raise AQUBContractV2Error(f"AQUB_V2_EXTRA_PROPERTY:{label}")
    if required - keys:
        raise AQUBContractV2Error(f"AQUB_V2_MISSING_PROPERTY:{label}")


def _assert_schema(value: Mapping[str, Any], expected: str) -> None:
    if value.get("schema_version") != expected:
        _fail("AQUB_V2_BAD_SCHEMA")


def _assert_string(
    value: object,
    label: str,
    *,
    minimum: int = 1,
    maximum: int = 256,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str):
        raise AQUBContractV2Error(f"AQUB_V2_BAD_STRING:{label}")
    if not minimum <= len(value) <= maximum or (
        pattern is not None and pattern.fullmatch(value) is None
    ):
        raise AQUBContractV2Error(f"AQUB_V2_BAD_STRING:{label}")
    return value


def _assert_opaque_ref(value: object, kind: str, label: str) -> str:
    reference = _assert_string(
        value,
        label,
        minimum=len(kind) + 9,
        maximum=len(kind) + 129,
    )
    prefix = f"{kind}:"
    if not reference.startswith(prefix) or _REFERENCE_SUFFIX_RE.fullmatch(
        reference[len(prefix) :]
    ) is None:
        raise AQUBContractV2Error(f"AQUB_V2_BAD_OPAQUE_REF:{label}")
    return reference


def _assert_nullable_opaque_ref(value: object, kind: str, label: str) -> None:
    if value is not None:
        _assert_opaque_ref(value, kind, label)


def _assert_uuid(value: object, label: str) -> None:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise AQUBContractV2Error(f"AQUB_V2_BAD_UUID:{label}")


def _assert_route_generation(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        _fail("AQUB_V2_BAD_ROUTE_GENERATION")


def _assert_datetime(value: object, label: str) -> None:
    timestamp = _assert_string(
        value,
        label,
        minimum=24,
        maximum=24,
        pattern=_CANONICAL_UTC_RE,
    )
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AQUBContractV2Error(f"AQUB_V2_BAD_DATETIME:{label}") from exc
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if canonical != timestamp:
        raise AQUBContractV2Error(f"AQUB_V2_BAD_DATETIME:{label}")


def _assert_idempotency_key(value: object) -> None:
    _assert_string(
        value,
        "idempotency_key",
        minimum=8,
        maximum=256,
        pattern=_IDEMPOTENCY_KEY_RE,
    )


def _assert_routing(value: Mapping[str, Any]) -> None:
    _assert_opaque_ref(
        value.get("installation_ref"),
        "installation",
        "installation_ref",
    )
    _assert_opaque_ref(value.get("cube_ref"), "cube", "cube_ref")
    _assert_route_generation(value.get("route_generation"))


def _assert_text_content(value: object, label: str) -> None:
    content = _object(value, label)
    _assert_exact_keys(content, _CONTENT_KEYS, _CONTENT_KEYS, label)
    if content["kind"] != "text":
        _fail("AQUB_V2_BAD_CONTENT_KIND")
    _assert_string(
        content["media_type"],
        f"{label}.media_type",
        minimum=3,
        maximum=129,
        pattern=_MEDIA_TYPE_RE,
    )
    _assert_string(content["text"], f"{label}.text", maximum=32_768)


def _assert_delivery_payload(value: object) -> None:
    payload = _object(value, "payload")
    _assert_exact_keys(payload, _PAYLOAD_KEYS, _PAYLOAD_KEYS, "payload")
    if payload["kind"] != "text":
        _fail("AQUB_V2_BAD_CONTENT_KIND")
    _assert_string(
        payload["media_type"],
        "payload.media_type",
        minimum=3,
        maximum=129,
        pattern=_MEDIA_TYPE_RE,
    )
    _assert_string(payload["text"], "payload.text", maximum=32_768)
    _assert_nullable_opaque_ref(
        payload["reply_to_message_ref"], "message", "payload.reply_to_message_ref"
    )


def _assert_reason_code(value: object, label: str = "reason_code") -> None:
    _assert_string(
        value,
        label,
        minimum=3,
        maximum=96,
        pattern=_REASON_CODE_RE,
    )


def _assert_retry_disposition(value: Mapping[str, Any], expected: str) -> None:
    if value.get("retry_disposition") != expected:
        _fail("AQUB_V2_BAD_RETRY_DISPOSITION")


def _assert_absent(value: Mapping[str, Any], fields: set[str]) -> None:
    if any(field_name in value for field_name in fields):
        _fail("AQUB_V2_CONFLICTING_DELIVERY_FACT")


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AQUBContractV2Error("AQUB_V2_BAD_JSON") from exc


def _decode_contract_json_bytes(payload: bytes) -> JsonObject:
    if type(payload) is not bytes:
        _fail("AQUB_V2_BAD_JSON_BYTES")
    if len(payload) > AQUB_V2_MAX_JSON_BYTES:
        _fail("AQUB_V2_JSON_TOO_LARGE")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except AQUBContractV2Error:
        raise
    except (UnicodeDecodeError, ValueError, OverflowError, RecursionError) as exc:
        raise AQUBContractV2Error("AQUB_V2_BAD_JSON_BYTES") from exc
    if not isinstance(value, dict):
        _fail("AQUB_V2_BAD_OBJECT")
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    value: JsonObject = {}
    for key, nested in pairs:
        if key in value:
            raise AQUBContractV2Error("AQUB_V2_DUPLICATE_PROPERTY")
        value[key] = nested
    return value


def _reject_json_constant(value: str) -> Never:
    raise AQUBContractV2Error(f"AQUB_V2_NON_FINITE_NUMBER:{value}")


def _string_value(value: object) -> str:
    if not isinstance(value, str):
        _fail("AQUB_V2_BAD_STRING")
    return value


def _integer_value(value: object) -> int:
    _assert_route_generation(value)
    assert isinstance(value, int)
    return value


def _fail(code: str) -> Never:
    raise AQUBContractV2Error(code)
