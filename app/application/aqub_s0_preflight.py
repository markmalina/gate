"""Default-off AQUB S0 Telegram preflight boundary.

This module deliberately stops at the Gate transport boundary.  It proves that
one exact bot/group/actor binding can own ``/aqub_ping`` and durably commit an
inbound command without invoking the AQUB domain engine, Telegram delivery, or
any legacy/canary dispatcher.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

AQUB_BOT_USERNAME = "@AQubAgentBot"
AQUB_PING_COMMAND = "/aqub_ping"
AQUB_OWNED_PATH = "/telegram/aqub/preflight"
AQUB_RECEIPT_SOURCE = "telegram_bot_api"

JsonObject = dict[str, Any]


class AQUBPreflightRole(StrEnum):
    ACE = "ace"
    ALPHA = "alpha"


class AQUBReceiptStatus(StrEnum):
    COMMITTED = "committed"


class AQUBStoreDisposition(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"


class AQUBDeliveryStatus(StrEnum):
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True, repr=False)
class AQUBPreflightBinding:
    """Release-pinned owner-only binding; blank defaults keep it dormant."""

    enabled: bool = False
    bot_username: str = ""
    group_chat_id: str = field(default="", repr=False)
    ace_user_id: str = field(default="", repr=False)
    alpha_user_id: str = field(default="", repr=False)
    ready_at: datetime | None = None
    receipt_signing_key: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        if self.bot_username != AQUB_BOT_USERNAME:
            raise ValueError("aqub_bot_identity_mismatch")
        if not self.group_chat_id.strip():
            raise ValueError("aqub_group_binding_required")
        if not self.ace_user_id.strip() or not self.alpha_user_id.strip():
            raise ValueError("aqub_actor_bindings_required")
        if self.ace_user_id == self.alpha_user_id:
            raise ValueError("aqub_actor_bindings_must_be_distinct")
        if self.ready_at is None or self.ready_at.utcoffset() is None:
            raise ValueError("aqub_ready_at_required")
        if len(self.receipt_signing_key) < 32:
            raise ValueError("aqub_receipt_signing_key_too_short")


@dataclass(frozen=True, slots=True, repr=False)
class AQUBPreflightInbound:
    """Trusted ingress projection. Provider identifiers never enter receipts."""

    bot_username: str
    external_id: str = field(repr=False)
    chat_id: str = field(repr=False)
    actor_id: str = field(repr=False)
    command: str = field(repr=False)
    payload: Mapping[str, Any] = field(repr=False)
    occurred_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class AQUBPreflightCommand:
    """Raw durable-store request, never suitable for logs or receipts."""

    source: str
    external_id: str = field(repr=False)
    payload_sha256: str
    payload: Mapping[str, Any] = field(repr=False)
    chat_id: str = field(repr=False)
    actor_id: str = field(repr=False)
    role: AQUBPreflightRole
    command: str = field(repr=False)
    occurred_at: datetime
    received_at: datetime
    owned_path: str = AQUB_OWNED_PATH


@dataclass(frozen=True, slots=True, repr=False)
class AQUBDurableAcceptance:
    """Store result returned only after the inbound command is durable."""

    status: AQUBReceiptStatus
    disposition: AQUBStoreDisposition
    command_receipt_id: str = field(repr=False)
    root_event_id: str = field(repr=False)
    correlation_id: str = field(repr=False)
    causation_id: str = field(repr=False)


class AQUBPreflightStore(Protocol):
    async def accept(self, command: AQUBPreflightCommand) -> AQUBDurableAcceptance: ...


@dataclass(frozen=True, slots=True)
class AQUBCommittedReceipt:
    """Signed and redacted evidence of one committed Gate command receipt."""

    source: str
    external_id: str
    payload_sha256: str
    status: str
    command_receipt_ref: str
    root_event_ref: str
    correlation_id: str
    causation_id: str
    role: str
    chat_ref: str
    actor_ref: str
    occurred_at: str
    received_at: str
    owned_path: str
    duplicate: bool
    result_json: Mapping[str, Any]
    delivery_status: str
    signature: str

    def unsigned_dict(self) -> JsonObject:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "payload_sha256": self.payload_sha256,
            "status": self.status,
            "command_receipt_ref": self.command_receipt_ref,
            "root_event_ref": self.root_event_ref,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "role": self.role,
            "chat_ref": self.chat_ref,
            "actor_ref": self.actor_ref,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
            "owned_path": self.owned_path,
            "duplicate": self.duplicate,
            "result_json": dict(self.result_json),
            "delivery_status": self.delivery_status,
        }

    def verify(self, signing_key: bytes) -> bool:
        expected = _signature(signing_key, self.unsigned_dict())
        return hmac.compare_digest(self.signature, expected)


@dataclass(frozen=True, slots=True)
class AQUBPreflightResult:
    owned: bool
    accepted: bool
    reason: str
    reply_text: str | None = None
    receipt: AQUBCommittedReceipt | None = None
    legacy_dispatch: bool | None = None
    canary_dispatch: bool | None = None


class AQUBS0PreflightAdapter:
    """Own only the exact AQUB S0 binding and never perform provider effects."""

    def __init__(
        self,
        *,
        binding: AQUBPreflightBinding,
        store: AQUBPreflightStore,
    ) -> None:
        self._binding = binding
        self._store = store

    async def handle(self, inbound: AQUBPreflightInbound) -> AQUBPreflightResult:
        role = self._bound_role(inbound)
        if role is None:
            return AQUBPreflightResult(
                owned=False,
                accepted=False,
                reason="aqub_route_not_owned",
            )

        if not _aware(inbound.occurred_at) or not _aware(inbound.received_at):
            return _owned_rejection("aqub_timestamp_invalid")
        ready_at = self._binding.ready_at
        if ready_at is None or inbound.occurred_at < ready_at:
            return _owned_rejection("aqub_event_before_ready")
        if inbound.received_at < inbound.occurred_at:
            return _owned_rejection("aqub_timestamp_order_invalid")
        if inbound.command != AQUB_PING_COMMAND:
            return _owned_rejection("aqub_command_unsupported")

        payload_sha256 = hashlib.sha256(_canonical(inbound.payload)).hexdigest()
        acceptance = await self._store.accept(
            AQUBPreflightCommand(
                source=AQUB_RECEIPT_SOURCE,
                external_id=inbound.external_id,
                payload_sha256=payload_sha256,
                payload=inbound.payload,
                chat_id=inbound.chat_id,
                actor_id=inbound.actor_id,
                role=role,
                command=inbound.command,
                occurred_at=inbound.occurred_at,
                received_at=inbound.received_at,
            )
        )
        _validate_acceptance(acceptance)

        duplicate = acceptance.disposition is AQUBStoreDisposition.DUPLICATE
        redacted_result: JsonObject = {
            "disposition": acceptance.disposition.value,
            "command": "aqub_ping",
        }
        unsigned: JsonObject = {
            "source": AQUB_RECEIPT_SOURCE,
            "external_id": _opaque_ref(
                self._binding.receipt_signing_key,
                "external",
                inbound.external_id,
            ),
            "payload_sha256": payload_sha256,
            "status": AQUBReceiptStatus.COMMITTED.value,
            "command_receipt_ref": _opaque_ref(
                self._binding.receipt_signing_key,
                "receipt",
                acceptance.command_receipt_id,
            ),
            "root_event_ref": _opaque_ref(
                self._binding.receipt_signing_key,
                "root_event",
                acceptance.root_event_id,
            ),
            "correlation_id": _opaque_ref(
                self._binding.receipt_signing_key,
                "correlation",
                acceptance.correlation_id,
            ),
            "causation_id": _opaque_ref(
                self._binding.receipt_signing_key,
                "causation",
                acceptance.causation_id,
            ),
            "role": role.value,
            "chat_ref": _opaque_ref(
                self._binding.receipt_signing_key,
                "chat",
                inbound.chat_id,
            ),
            "actor_ref": _opaque_ref(
                self._binding.receipt_signing_key,
                "actor",
                inbound.actor_id,
            ),
            "occurred_at": inbound.occurred_at.isoformat(),
            "received_at": inbound.received_at.isoformat(),
            "owned_path": AQUB_OWNED_PATH,
            "duplicate": duplicate,
            "result_json": redacted_result,
            # A committed command receipt is not evidence of Telegram delivery.
            "delivery_status": AQUBDeliveryStatus.NOT_RUN.value,
        }
        receipt = AQUBCommittedReceipt(
            source=AQUB_RECEIPT_SOURCE,
            external_id=str(unsigned["external_id"]),
            payload_sha256=payload_sha256,
            status="committed",
            command_receipt_ref=str(unsigned["command_receipt_ref"]),
            root_event_ref=str(unsigned["root_event_ref"]),
            correlation_id=str(unsigned["correlation_id"]),
            causation_id=str(unsigned["causation_id"]),
            role=role.value,
            chat_ref=str(unsigned["chat_ref"]),
            actor_ref=str(unsigned["actor_ref"]),
            occurred_at=inbound.occurred_at.isoformat(),
            received_at=inbound.received_at.isoformat(),
            owned_path=AQUB_OWNED_PATH,
            duplicate=duplicate,
            result_json=redacted_result,
            delivery_status=AQUBDeliveryStatus.NOT_RUN.value,
            signature=_signature(self._binding.receipt_signing_key, unsigned),
        )
        return AQUBPreflightResult(
            owned=True,
            accepted=True,
            reason="aqub_ping_committed",
            reply_text=f"AQUB S0: связь {role.value} подтверждена.",
            receipt=receipt,
            legacy_dispatch=False,
            canary_dispatch=False,
        )

    def _bound_role(self, inbound: AQUBPreflightInbound) -> AQUBPreflightRole | None:
        binding = self._binding
        if (
            not binding.enabled
            or inbound.bot_username != AQUB_BOT_USERNAME
            or inbound.bot_username != binding.bot_username
            or inbound.chat_id != binding.group_chat_id
        ):
            return None
        if inbound.actor_id == binding.ace_user_id:
            return AQUBPreflightRole.ACE
        if inbound.actor_id == binding.alpha_user_id:
            return AQUBPreflightRole.ALPHA
        return None


def _owned_rejection(reason: str) -> AQUBPreflightResult:
    return AQUBPreflightResult(
        owned=True,
        accepted=False,
        reason=reason,
        legacy_dispatch=False,
        canary_dispatch=False,
    )


def _validate_acceptance(acceptance: AQUBDurableAcceptance) -> None:
    if acceptance.status is not AQUBReceiptStatus.COMMITTED:
        raise RuntimeError("aqub_store_not_committed")
    for value in (
        acceptance.command_receipt_id,
        acceptance.root_event_id,
        acceptance.correlation_id,
        acceptance.causation_id,
    ):
        if not value or len(value) > 1_024:
            raise RuntimeError("aqub_store_reference_invalid")


def _opaque_ref(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(signing_key, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}:{digest}"


def _signature(signing_key: bytes, value: Mapping[str, Any]) -> str:
    return hmac.new(signing_key, _canonical(value), hashlib.sha256).hexdigest()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _aware(value: datetime) -> bool:
    return value.utcoffset() is not None
