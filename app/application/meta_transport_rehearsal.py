"""Production-inactive Meta transport rehearsal over canonical Gate contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

OPAQUE_REF_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)


class MetaTransportRehearsalError(RuntimeError):
    """A bounded fail-closed rehearsal refusal."""


class SimulatedDeliveryOutcome(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SimulatedProviderResult:
    outcome: SimulatedDeliveryOutcome
    provider_receipt_ref: str


class SimulatedMetaProvider(Protocol):
    """Fake-provider seam. A live adapter is deliberately outside this slice."""

    def deliver(
        self,
        *,
        delivery_intent_ref: str,
        approval_ref: str,
        portfolio_run_id: str,
    ) -> SimulatedProviderResult: ...


@dataclass(frozen=True, slots=True)
class MetaTransportRehearsalCommand:
    workspace_key: str
    portfolio_run_id: str
    grant: dict[str, Any]
    now: datetime
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class MetaTransportRehearsalResult:
    delivery_fact: dict[str, Any]
    idempotent_replay: bool
    external_delivery: bool = False


@dataclass(frozen=True, slots=True)
class _ReplayRecord:
    fingerprint: str
    delivery_fact: dict[str, Any]


class MemoryMetaRehearsalReplayStore:
    """Deterministic rehearsal store; it carries no production activation claim."""

    def __init__(self) -> None:
        self._records: dict[str, _ReplayRecord] = {}

    def get(self, idempotency_key: str) -> _ReplayRecord | None:
        return self._records.get(idempotency_key)

    def put(self, idempotency_key: str, record: _ReplayRecord) -> None:
        if idempotency_key in self._records:
            raise MetaTransportRehearsalError("meta_rehearsal_replay_race")
        self._records[idempotency_key] = record


class DeterministicFakeMetaProvider:
    """Network-free provider used only by the portfolio golden rehearsal."""

    def __init__(
        self,
        *,
        outcome: SimulatedDeliveryOutcome = SimulatedDeliveryOutcome.DELIVERED,
    ) -> None:
        self.outcome = outcome
        self.call_count = 0

    def deliver(
        self,
        *,
        delivery_intent_ref: str,
        approval_ref: str,
        portfolio_run_id: str,
    ) -> SimulatedProviderResult:
        self.call_count += 1
        digest = _digest(
            {
                "delivery_intent_ref": delivery_intent_ref,
                "approval_ref": approval_ref,
                "portfolio_run_id": portfolio_run_id,
                "outcome": self.outcome.value,
            }
        )
        return SimulatedProviderResult(
            outcome=self.outcome,
            provider_receipt_ref=f"receipt_sim_{digest[:24]}",
        )


class RehearseMetaTransportService:
    """Consume external authority and emit one provider-neutral delivery fact."""

    def __init__(
        self,
        *,
        provider: SimulatedMetaProvider,
        replay_store: MemoryMetaRehearsalReplayStore,
    ) -> None:
        self._provider = provider
        self._replay_store = replay_store

    def execute(self, command: MetaTransportRehearsalCommand) -> MetaTransportRehearsalResult:
        if not command.enabled:
            raise MetaTransportRehearsalError("meta_rehearsal_disabled")
        _require_opaque_ref(command.workspace_key, "workspace_key")
        _require_opaque_ref(command.portfolio_run_id, "portfolio_run_id")
        if command.now.tzinfo is None:
            raise MetaTransportRehearsalError("meta_rehearsal_now_invalid")

        grant = _parse_grant(command.grant, now=command.now)
        fingerprint = _digest(
            {
                "workspace_key": command.workspace_key,
                "portfolio_run_id": command.portfolio_run_id,
                "grant": command.grant,
            }
        )
        previous = self._replay_store.get(grant.idempotency_key)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise MetaTransportRehearsalError("meta_rehearsal_idempotency_conflict")
            return MetaTransportRehearsalResult(
                delivery_fact=previous.delivery_fact,
                idempotent_replay=True,
            )

        provider_result = self._provider.deliver(
            delivery_intent_ref=grant.delivery_intent_ref,
            approval_ref=grant.approval_ref,
            portfolio_run_id=command.portfolio_run_id,
        )
        _require_opaque_ref(provider_result.provider_receipt_ref, "provider_receipt_ref")
        reconciliation_required = provider_result.outcome is SimulatedDeliveryOutcome.UNKNOWN
        attempt_digest = _digest(
            {
                "workspace_key": command.workspace_key,
                "portfolio_run_id": command.portfolio_run_id,
                "idempotency_key": grant.idempotency_key,
            }
        )
        fact = {
            "contract": "gate.delivery-fact",
            "version": "1.0.0",
            "event_id": f"evt_meta_sim_{attempt_digest[:24]}",
            "occurred_at": command.now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "idempotency_key": f"fact_{grant.idempotency_key}",
            "correlation_id": command.grant["correlation_id"],
            "trace_id": command.grant["trace_id"],
            "payload": {
                "delivery_intent_ref": grant.delivery_intent_ref,
                "attempt_ref": f"attempt_sim_{attempt_digest[:24]}",
                "outcome": provider_result.outcome.value,
                "provider_receipt_ref": provider_result.provider_receipt_ref,
                "reconciliation_required": reconciliation_required,
            },
        }
        self._replay_store.put(
            grant.idempotency_key,
            _ReplayRecord(fingerprint=fingerprint, delivery_fact=fact),
        )
        return MetaTransportRehearsalResult(delivery_fact=fact, idempotent_replay=False)


@dataclass(frozen=True, slots=True)
class _ParsedGrant:
    idempotency_key: str
    delivery_intent_ref: str
    approval_ref: str


def _parse_grant(value: dict[str, Any], *, now: datetime) -> _ParsedGrant:
    required = {
        "contract",
        "version",
        "event_id",
        "occurred_at",
        "idempotency_key",
        "correlation_id",
        "trace_id",
        "payload",
    }
    if set(value) != required:
        raise MetaTransportRehearsalError("meta_rehearsal_grant_envelope_invalid")
    if value.get("contract") != "gate.delivery-grant" or value.get("version") != "1.0.0":
        raise MetaTransportRehearsalError("meta_rehearsal_grant_contract_invalid")
    for field_name in ("event_id", "idempotency_key", "correlation_id", "trace_id"):
        _require_opaque_ref(value.get(field_name), field_name)

    payload = value.get("payload")
    if not isinstance(payload, dict) or set(payload) != {
        "delivery_intent_ref",
        "approval_ref",
        "authorized",
        "expires_at",
    }:
        raise MetaTransportRehearsalError("meta_rehearsal_grant_payload_invalid")
    if payload.get("authorized") is not True:
        raise MetaTransportRehearsalError("meta_rehearsal_grant_not_authorized")
    delivery_intent_ref = _require_opaque_ref(
        payload.get("delivery_intent_ref"), "delivery_intent_ref"
    )
    approval_ref = _require_opaque_ref(payload.get("approval_ref"), "approval_ref")
    try:
        expires_at = datetime.fromisoformat(str(payload.get("expires_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetaTransportRehearsalError("meta_rehearsal_grant_expiry_invalid") from exc
    if expires_at.tzinfo is None or expires_at <= now:
        raise MetaTransportRehearsalError("meta_rehearsal_grant_expired")
    return _ParsedGrant(
        idempotency_key=str(value["idempotency_key"]),
        delivery_intent_ref=delivery_intent_ref,
        approval_ref=approval_ref,
    )


def _require_opaque_ref(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not 3 <= len(value) <= 128
        or value != value.strip()
        or any(character not in OPAQUE_REF_CHARACTERS for character in value)
    ):
        raise MetaTransportRehearsalError(f"meta_rehearsal_{field_name}_invalid")
    return value


def _digest(value: object) -> str:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(wire.encode()).hexdigest()
