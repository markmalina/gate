"""Fail-closed, provider-free routing journal for synthetic AQUB rehearsals.

The module owns no network client, environment lookup, database connection or
runtime wiring.  It records local facts only; in particular, preparing a
response never claims that an external delivery happened.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Never, TypeVar

JsonObject = Mapping[str, Any]
T = TypeVar("T")

ACTIVE_SYNTHETIC_SPACES = frozenset({"test", "pilot-a", "pilot-b"})
_REF = re.compile(r"^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._~-]{7,127}$")
_SOURCE_EVENT = re.compile(r"^[a-z0-9][a-z0-9._:~/-]{7,255}$")


class RouteError(RuntimeError):
    """Stable fail-closed refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IngressDisposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class RouteStage(StrEnum):
    ACCEPTED = "accepted"
    COMMITTED = "committed"
    RESPONSE_PREPARED = "response_prepared"
    DELIVERED = "delivered"


@dataclass(frozen=True, slots=True)
class RouteBinding:
    route_ref: str
    space_ref: str
    allowed_actor_refs: frozenset[str]

    def __post_init__(self) -> None:
        _ref(self.route_ref, "ROUTE_REF_INVALID", "route")
        _space(self.space_ref)
        if not self.allowed_actor_refs:
            _fail("ROUTE_ACTOR_ALLOWLIST_EMPTY")
        for actor_ref in self.allowed_actor_refs:
            _ref(actor_ref, "ACTOR_REF_INVALID", "actor")


@dataclass(frozen=True, slots=True)
class SourceEnvelope:
    route_ref: str
    space_ref: str
    actor_ref: str
    source_event_id: str
    payload: JsonObject

    def __post_init__(self) -> None:
        _ref(self.route_ref, "ROUTE_REF_INVALID", "route")
        _space(self.space_ref)
        _ref(self.actor_ref, "ACTOR_REF_INVALID", "actor")
        if _SOURCE_EVENT.fullmatch(self.source_event_id) is None:
            _fail("SOURCE_EVENT_ID_INVALID")
        object.__setattr__(self, "payload", _freeze_object(self.payload, "PAYLOAD_INVALID"))


@dataclass(frozen=True, slots=True)
class JournalEntry:
    ingress_ref: str
    route_ref: str
    space_ref: str
    actor_ref: str
    source_event_id: str
    payload_digest: str
    stage: RouteStage
    response_ref: str | None = None
    response_digest: str | None = None
    delivery_ref: str | None = None


@dataclass(frozen=True, slots=True)
class IngressReceipt:
    disposition: IngressDisposition
    entry: JournalEntry


class RouteRegistry:
    """Exact route registry; it has no default or fuzzy lookup path."""

    def __init__(self, bindings: tuple[RouteBinding, ...]) -> None:
        by_ref: dict[str, RouteBinding] = {}
        for binding in bindings:
            if binding.route_ref in by_ref:
                _fail("ROUTE_AMBIGUOUS")
            by_ref[binding.route_ref] = binding
        self._bindings = MappingProxyType(by_ref)

    def resolve(self, envelope: SourceEnvelope) -> RouteBinding:
        binding = self._bindings.get(envelope.route_ref)
        if binding is None:
            _fail("ROUTE_UNKNOWN")
        if envelope.space_ref != binding.space_ref:
            _fail("CROSS_SPACE_ENVELOPE")
        if envelope.actor_ref not in binding.allowed_actor_refs:
            _fail("ACTOR_NOT_ALLOWED")
        return binding


class LocalRouteJournal:
    """Deterministic in-memory adapter for provider-free repository checks."""

    def __init__(self, registry: RouteRegistry) -> None:
        self._registry = registry
        self._entries: dict[tuple[str, str, str], JournalEntry] = {}
        self._envelopes: dict[tuple[str, str, str], bytes] = {}

    def accept(self, envelope: SourceEnvelope) -> IngressReceipt:
        self._registry.resolve(envelope)
        key = (envelope.space_ref, envelope.actor_ref, envelope.source_event_id)
        canonical = _canonical_envelope(envelope)
        existing = self._entries.get(key)
        if existing is not None:
            if self._envelopes[key] != canonical:
                _fail("SOURCE_EVENT_CONFLICT")
            return IngressReceipt(IngressDisposition.DUPLICATE, existing)
        ingress_ref = "ingress:" + hashlib.sha256(canonical).hexdigest()[:24]
        entry = JournalEntry(
            ingress_ref=ingress_ref,
            route_ref=envelope.route_ref,
            space_ref=envelope.space_ref,
            actor_ref=envelope.actor_ref,
            source_event_id=envelope.source_event_id,
            payload_digest=hashlib.sha256(_canonical(dict(envelope.payload))).hexdigest(),
            stage=RouteStage.ACCEPTED,
        )
        self._entries[key] = entry
        self._envelopes[key] = canonical
        return IngressReceipt(IngressDisposition.ACCEPTED, entry)

    def commit_with(self, ingress_ref: str, downstream: Callable[[], T]) -> tuple[JournalEntry, T]:
        key, entry = self._find(ingress_ref)
        if entry.stage is not RouteStage.ACCEPTED:
            _fail("INGRESS_NOT_ACCEPTED")
        try:
            result = downstream()
        except Exception as exc:
            raise RouteError("DOWNSTREAM_FAILURE") from exc
        committed = replace(entry, stage=RouteStage.COMMITTED)
        self._entries[key] = committed
        return committed, result

    def prepare_response(self, ingress_ref: str, response: JsonObject) -> JournalEntry:
        key, entry = self._find(ingress_ref)
        canonical = _canonical(dict(_freeze_object(response, "RESPONSE_INVALID")))
        digest = hashlib.sha256(canonical).hexdigest()
        response_ref = "response:" + hashlib.sha256(
            (ingress_ref + ":" + digest).encode("ascii")
        ).hexdigest()[:24]
        if entry.stage is RouteStage.RESPONSE_PREPARED:
            if entry.response_digest != digest:
                _fail("RESPONSE_CONFLICT")
            return entry
        if entry.stage is RouteStage.DELIVERED:
            if entry.response_digest != digest:
                _fail("RESPONSE_CONFLICT")
            return entry
        if entry.stage is not RouteStage.COMMITTED:
            _fail("INGRESS_NOT_COMMITTED")
        prepared = replace(
            entry,
            stage=RouteStage.RESPONSE_PREPARED,
            response_ref=response_ref,
            response_digest=digest,
        )
        self._entries[key] = prepared
        return prepared

    def record_delivery(self, response_ref: str, delivery_ref: str) -> JournalEntry:
        _ref(delivery_ref, "DELIVERY_REF_INVALID", "delivery")
        key, entry = self._find_response(response_ref)
        if entry.stage is RouteStage.DELIVERED:
            if entry.delivery_ref != delivery_ref:
                _fail("DELIVERY_CONFLICT")
            return entry
        if entry.stage is not RouteStage.RESPONSE_PREPARED:
            _fail("RESPONSE_NOT_PREPARED")
        delivered = replace(entry, stage=RouteStage.DELIVERED, delivery_ref=delivery_ref)
        self._entries[key] = delivered
        return delivered

    def get(self, ingress_ref: str) -> JournalEntry:
        return self._find(ingress_ref)[1]

    def _find(self, ingress_ref: str) -> tuple[tuple[str, str, str], JournalEntry]:
        for key, entry in self._entries.items():
            if entry.ingress_ref == ingress_ref:
                return key, entry
        _fail("INGRESS_UNKNOWN")

    def _find_response(self, response_ref: str) -> tuple[tuple[str, str, str], JournalEntry]:
        for key, entry in self._entries.items():
            if entry.response_ref == response_ref:
                return key, entry
        _fail("RESPONSE_UNKNOWN")


def _freeze_object(value: JsonObject, code: str) -> JsonObject:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        _fail(code)
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise RouteError(code) from exc
    if not isinstance(copied, dict):
        _fail(code)
    return MappingProxyType(copied)


def _canonical_envelope(envelope: SourceEnvelope) -> bytes:
    return _canonical(
        {
            "route_ref": envelope.route_ref,
            "space_ref": envelope.space_ref,
            "actor_ref": envelope.actor_ref,
            "source_event_id": envelope.source_event_id,
            "payload": dict(envelope.payload),
        }
    )


def _canonical(value: JsonObject) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _space(value: str) -> None:
    if value not in ACTIVE_SYNTHETIC_SPACES:
        _fail("SPACE_NOT_ALLOWED")


def _ref(value: str, code: str, prefix: str) -> None:
    if type(value) is not str:
        _fail(code)
    if _REF.fullmatch(value) is None or not value.startswith(prefix + ":"):
        _fail(code)


def _fail(code: str) -> Never:
    raise RouteError(code)
