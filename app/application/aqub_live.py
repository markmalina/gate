"""Signed AQUB live transport contracts layered on the isolated preflight route."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.application.aqub_s0_preflight import (
    AQUB_BOT_USERNAME,
    AQUBPreflightBinding,
    AQUBPreflightCommand,
    AQUBPreflightInbound,
    AQUBPreflightRole,
    AQUBPreflightStore,
    AQUBStoreDisposition,
)
from app.application.telegram_processing import canonical_telegram_payload_digest

AQUB_GATE_ENVELOPE_SCHEMA = "aqub.gate-envelope.v1"
AQUB_DELIVERY_COMMAND_SCHEMA = "aqub.delivery-command.v1"
AQUB_PROGRAM_PROMPT_TEXT = (
    "AQUB: пришлите программу ответом на это сообщение по шаблону из минимум двух шагов "
    "(до 3500 символов)."
)
AQUB_RUNTIME_INBOUND_PATH = "/gate/inbound"
AQUB_GATE_DELIVERY_PATH = "/internal/aqub/deliver"

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class AQUBLiveForwardResult:
    accepted: bool
    duplicate: bool
    reason: str
    downstream: JsonObject | None = None


class AQUBRuntimeClient(Protocol):
    async def post_envelope(
        self,
        *,
        raw_body: bytes,
        signature: str,
    ) -> JsonObject: ...


class HttpAQUBRuntimeClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 8.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def post_envelope(self, *, raw_body: bytes, signature: str) -> JsonObject:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}{AQUB_RUNTIME_INBOUND_PATH}",
                content=raw_body,
                headers={
                    "content-type": "application/json",
                    "x-aqub-signature": signature,
                },
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"aqub_runtime_http_{response.status_code}")
        value = response.json()
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise RuntimeError("aqub_runtime_response_invalid")
        return value


class AQUBLiveIngressAdapter:
    """Commit an owned Telegram update in Gate, then forward a redacted signed envelope."""

    def __init__(
        self,
        *,
        binding: AQUBPreflightBinding,
        store: AQUBPreflightStore,
        runtime: AQUBRuntimeClient,
    ) -> None:
        self._binding = binding
        self._store = store
        self._runtime = runtime

    async def handle(self, inbound: AQUBPreflightInbound) -> AQUBLiveForwardResult:
        role = _bound_role(self._binding, inbound)
        if role is None:
            return AQUBLiveForwardResult(
                accepted=False,
                duplicate=False,
                reason="aqub_route_not_owned",
            )
        _validate_time(self._binding, inbound)

        payload = dict(inbound.payload)
        payload_sha256 = canonical_telegram_payload_digest(payload)
        acceptance = await self._store.accept(
            AQUBPreflightCommand(
                source="telegram_bot_api",
                external_id=inbound.external_id,
                payload_sha256=payload_sha256,
                payload=payload,
                chat_id=inbound.chat_id,
                actor_id=inbound.actor_id,
                role=role,
                command=inbound.command,
                occurred_at=inbound.occurred_at,
                received_at=inbound.received_at,
            )
        )

        raw_body = _build_signed_envelope_body(
            binding=self._binding,
            inbound=inbound,
            role=role,
            command_receipt_id=acceptance.command_receipt_id,
            payload_sha256=payload_sha256,
        )
        signature = hmac.new(
            self._binding.receipt_signing_key,
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        downstream = await self._runtime.post_envelope(
            raw_body=raw_body,
            signature=signature,
        )
        return AQUBLiveForwardResult(
            accepted=True,
            duplicate=acceptance.disposition is AQUBStoreDisposition.DUPLICATE,
            reason="aqub_live_committed",
            downstream=downstream,
        )


def _bound_role(
    binding: AQUBPreflightBinding,
    inbound: AQUBPreflightInbound,
) -> AQUBPreflightRole | None:
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


def _validate_time(binding: AQUBPreflightBinding, inbound: AQUBPreflightInbound) -> None:
    if inbound.occurred_at.utcoffset() is None or inbound.received_at.utcoffset() is None:
        raise RuntimeError("aqub_timestamp_invalid")
    if binding.ready_at is None or inbound.occurred_at < binding.ready_at:
        raise RuntimeError("aqub_event_before_ready")
    if inbound.received_at < inbound.occurred_at:
        raise RuntimeError("aqub_timestamp_order_invalid")


def _opaque_ref(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(signing_key, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"{kind}:{digest}"


def _reply_context(payload: Mapping[str, Any]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, Mapping):
        return None
    reply = message.get("reply_to_message")
    if not isinstance(reply, Mapping):
        return None
    sender = reply.get("from")
    if (
        isinstance(sender, Mapping)
        and sender.get("is_bot") is True
        and reply.get("text") == AQUB_PROGRAM_PROMPT_TEXT
    ):
        return "program_prompt"
    return None


def _build_signed_envelope_body(
    *,
    binding: AQUBPreflightBinding,
    inbound: AQUBPreflightInbound,
    role: AQUBPreflightRole,
    command_receipt_id: str,
    payload_sha256: str,
) -> bytes:
    message = inbound.payload.get("message")
    if not isinstance(message, Mapping) or type(message.get("message_id")) is not int:
        raise RuntimeError("aqub_message_id_invalid")
    reply = message.get("reply_to_message")
    reply_message_id: int | None = None
    if isinstance(reply, Mapping) and type(reply.get("message_id")) is int:
        reply_message_id = reply["message_id"]

    envelope: JsonObject = {
        "schema_version": AQUB_GATE_ENVELOPE_SCHEMA,
        "occurred_at": inbound.received_at.isoformat(),
        "source_occurred_at": inbound.occurred_at.isoformat(),
        "chat_ref": _opaque_ref(
            binding.receipt_signing_key,
            "chat",
            inbound.chat_id,
        ),
        "actor_ref": _opaque_ref(
            binding.receipt_signing_key,
            "actor",
            inbound.actor_id,
        ),
        "role": role.value,
        "update_id": _opaque_ref(
            binding.receipt_signing_key,
            "external",
            inbound.external_id,
        ),
        "message_id": _opaque_ref(
            binding.receipt_signing_key,
            "message",
            str(message["message_id"]),
        ),
        "reply_to_message_id": (
            _opaque_ref(
                binding.receipt_signing_key,
                "message",
                str(reply_message_id),
            )
            if reply_message_id is not None
            else None
        ),
        "reply_context": _reply_context(inbound.payload),
        "text": inbound.command,
        "transport_receipt_ref": _opaque_ref(
            binding.receipt_signing_key,
            "receipt",
            command_receipt_id,
        ),
        "payload_sha256": payload_sha256,
    }
    return json.dumps(
        envelope,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
