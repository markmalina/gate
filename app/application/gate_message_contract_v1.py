"""Provider-neutral Gate message contract preflight.

This module is deliberately absent from application composition.  It has no
environment, database, provider, endpoint, worker or product dependency.  The
only executable adapter mode is an explicit synthetic-rehearsal enum member.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Never

JsonObject = dict[str, Any]
GATE_MESSAGE_V1_MAX_BYTES = 256 * 1024
GATE_MESSAGE_V1_MAX_RETRIES = 3

_REF = re.compile(r"^[a-z][a-z0-9_-]{1,31}:[A-Za-z0-9._~-]{8,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:[0-5]\d\.\d{3}Z$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z][A-Za-z0-9._:~/-]{7,255}$")


class GateMessageContractError(RuntimeError):
    """Fail-closed contract refusal with a stable machine-readable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GateMessageAdapterMode(StrEnum):
    DISABLED = "disabled"
    SYNTHETIC_REHEARSAL = "synthetic_rehearsal"


class DeliveryDisposition(StrEnum):
    SETTLED = "settled"
    RETRY = "retry"
    RECONCILE_ONLY = "reconcile_only"


@dataclass(frozen=True, slots=True)
class GateMessageDocument:
    schema_version: str
    canonical_json: bytes

    def to_wire(self) -> JsonObject:
        parsed = _decode(self.canonical_json)
        checked = parse_gate_message_object_v1(parsed)
        if checked.schema_version != self.schema_version:
            _fail("GATE_MESSAGE_DOCUMENT_INVALID")
        return parsed


@dataclass(frozen=True, slots=True)
class GateMessageAdapterV1:
    mode: GateMessageAdapterMode = GateMessageAdapterMode.DISABLED

    def __post_init__(self) -> None:
        if type(self.mode) is not GateMessageAdapterMode:
            _fail("GATE_MESSAGE_ADAPTER_MODE_INVALID")

    def rehearse(self, value: Mapping[str, Any]) -> GateMessageDocument:
        if self.mode is not GateMessageAdapterMode.SYNTHETIC_REHEARSAL:
            _fail("GATE_MESSAGE_ADAPTER_DISABLED")
        return parse_gate_message_object_v1(value)


def parse_gate_message_json_v1(raw: bytes) -> GateMessageDocument:
    if type(raw) is not bytes or len(raw) > GATE_MESSAGE_V1_MAX_BYTES:
        _fail("GATE_MESSAGE_JSON_SIZE_INVALID")
    return parse_gate_message_object_v1(_decode(raw))


def parse_gate_message_object_v1(value: Mapping[str, Any]) -> GateMessageDocument:
    obj = _object(value, "document")
    version = _string(obj.get("schema_version"), "schema_version", 1, 64)
    parser: Callable[[JsonObject], None]
    if version == "gate.message.received.v1":
        parser = _validate_received
    elif version == "gate.message.send.v1":
        parser = _validate_send
    elif version == "gate.delivery.fact.v1":
        parser = _validate_delivery
    else:
        _fail("GATE_MESSAGE_SCHEMA_UNSUPPORTED")
    parser(obj)
    canonical = json.dumps(
        obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if len(canonical) > GATE_MESSAGE_V1_MAX_BYTES:
        _fail("GATE_MESSAGE_JSON_SIZE_INVALID")
    return GateMessageDocument(version, canonical)


def serialize_gate_message_json_v1(document: GateMessageDocument) -> bytes:
    return parse_gate_message_object_v1(document.to_wire()).canonical_json


def delivery_disposition_v1(fact: Mapping[str, Any]) -> DeliveryDisposition:
    wire = parse_gate_message_object_v1(fact).to_wire()
    if wire["schema_version"] != "gate.delivery.fact.v1":
        _fail("GATE_DELIVERY_FACT_REQUIRED")
    status = wire["status"]
    if status == "retryable":
        if wire["attempt"] >= GATE_MESSAGE_V1_MAX_RETRIES:
            return DeliveryDisposition.SETTLED
        return DeliveryDisposition.RETRY
    if status == "unknown":
        return DeliveryDisposition.RECONCILE_ONLY
    return DeliveryDisposition.SETTLED


def preserve_legacy_result_on_shadow_failure(
    legacy_result: Any, shadow: Callable[[], object]
) -> Any:
    """Run an isolated rehearsal without allowing its failure to affect legacy."""

    try:
        shadow()
    except RuntimeError:
        return legacy_result
    return legacy_result


def _validate_received(obj: JsonObject) -> None:
    required = {
        "schema_version",
        "event_ref",
        "idempotency_key",
        "occurred_at",
        "installation_ref",
        "conversation_ref",
        "actor_ref",
        "destination_ref",
        "route_generation",
        "content",
    }
    _exact(obj, required, required, "received")
    _common_route(obj)
    _ref(obj["event_ref"], "event_ref", "event")
    _idempotency(obj["idempotency_key"])
    _time(obj["occurred_at"], "occurred_at")
    _ref(obj["actor_ref"], "actor_ref", "actor")
    _content(obj["content"])


def _validate_send(obj: JsonObject) -> None:
    required = {
        "schema_version",
        "command_ref",
        "idempotency_key",
        "requested_at",
        "installation_ref",
        "conversation_ref",
        "destination_ref",
        "route_generation",
        "payload",
    }
    _exact(obj, required, required, "send")
    _common_route(obj)
    _ref(obj["command_ref"], "command_ref", "command")
    _idempotency(obj["idempotency_key"])
    _time(obj["requested_at"], "requested_at")
    _content(obj["payload"])


def _validate_delivery(obj: JsonObject) -> None:
    required = {
        "schema_version",
        "fact_ref",
        "command_ref",
        "idempotency_key",
        "observed_at",
        "installation_ref",
        "conversation_ref",
        "destination_ref",
        "route_generation",
        "attempt",
        "status",
        "consumer_commit_ref",
        "reconciliation_ref",
    }
    _exact(obj, required, required, "delivery")
    _common_route(obj)
    _ref(obj["fact_ref"], "fact_ref", "fact")
    _ref(obj["command_ref"], "command_ref", "command")
    _idempotency(obj["idempotency_key"])
    _time(obj["observed_at"], "observed_at")
    attempt = obj["attempt"]
    if type(attempt) is not int or not 1 <= attempt <= GATE_MESSAGE_V1_MAX_RETRIES:
        _fail("GATE_MESSAGE_ATTEMPT_INVALID")
    status = obj["status"]
    if status not in {"committed", "duplicate", "rejected", "retryable", "unknown"}:
        _fail("GATE_MESSAGE_STATUS_INVALID")
    commit = obj["consumer_commit_ref"]
    reconciliation = obj["reconciliation_ref"]
    if status in {"committed", "duplicate"}:
        _ref(commit, "consumer_commit_ref", "commit")
    elif commit is not None:
        _fail("GATE_MESSAGE_CONSUMER_COMMIT_FORBIDDEN")
    if status == "unknown":
        _ref(reconciliation, "reconciliation_ref", "reconciliation")
    elif reconciliation is not None:
        _fail("GATE_MESSAGE_RECONCILIATION_FORBIDDEN")


def _common_route(obj: JsonObject) -> None:
    _ref(obj["installation_ref"], "installation_ref", "installation")
    _ref(obj["conversation_ref"], "conversation_ref", "conversation")
    _ref(obj["destination_ref"], "destination_ref", "destination")
    generation = obj["route_generation"]
    if type(generation) is not int or not 1 <= generation <= 2_147_483_647:
        _fail("GATE_MESSAGE_ROUTE_GENERATION_INVALID")


def _content(value: Any) -> None:
    obj = _object(value, "content")
    kind = obj.get("kind")
    if kind == "text":
        required = {"kind", "text"}
        _exact(obj, required, required, "content")
        _string(obj["text"], "text", 1, 32768)
        return
    if kind == "media_ref":
        required = {"kind", "object_ref", "sha256", "size_bytes", "content_type"}
        _exact(obj, required, required, "content")
        _ref(obj["object_ref"], "object_ref", "object")
        _string(obj["sha256"], "sha256", 64, 64, _SHA256)
        size = obj["size_bytes"]
        if type(size) is not int or not 1 <= size <= 25 * 1024 * 1024:
            _fail("GATE_MESSAGE_MEDIA_SIZE_INVALID")
        media_type = _string(obj["content_type"], "content_type", 3, 127)
        if "/" not in media_type or "://" in media_type:
            _fail("GATE_MESSAGE_CONTENT_TYPE_INVALID")
        return
    _fail("GATE_MESSAGE_CONTENT_KIND_INVALID")


def _decode(raw: bytes) -> JsonObject:
    def pairs(items: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in items:
            if key in result:
                _fail("GATE_MESSAGE_JSON_DUPLICATE_PROPERTY")
            result[key] = value
        return result

    def constant(_value: str) -> Never:
        _fail("GATE_MESSAGE_JSON_NONFINITE")

    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateMessageContractError("GATE_MESSAGE_JSON_INVALID") from exc
    return _object(decoded, "document")


def _object(value: Any, field: str) -> JsonObject:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        _fail(f"GATE_MESSAGE_{field.upper()}_OBJECT_REQUIRED")
    return dict(value)


def _exact(value: JsonObject, allowed: set[str], required: set[str], field: str) -> None:
    if set(value) - allowed:
        _fail(f"GATE_MESSAGE_{field.upper()}_UNKNOWN_FIELD")
    if required - set(value):
        _fail(f"GATE_MESSAGE_{field.upper()}_MISSING_FIELD")


def _string(
    value: Any,
    field: str,
    minimum: int,
    maximum: int,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if type(value) is not str or not minimum <= len(value) <= maximum:
        _fail(f"GATE_MESSAGE_{field.upper()}_INVALID")
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail(f"GATE_MESSAGE_{field.upper()}_INVALID")
    return value


def _ref(value: Any, field: str, prefix: str) -> str:
    result = _string(value, field, len(prefix) + 10, 160, _REF)
    if not result.startswith(prefix + ":"):
        _fail(f"GATE_MESSAGE_{field.upper()}_INVALID")
    return result


def _idempotency(value: Any) -> None:
    _string(value, "idempotency_key", 8, 256, _IDEMPOTENCY)


def _time(value: Any, field: str) -> None:
    _string(value, field, 24, 24, _TIME)


def _fail(code: str) -> Never:
    raise GateMessageContractError(code)
