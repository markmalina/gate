"""Signed AQUB live transport contracts layered on the isolated preflight route."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.application.aqub_food_media import AQUBFoodMediaQueue
from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUBDurableAcceptance,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
    AQUBTransportBinding,
    AQUBTransportCommand,
    AQUBTransportContentKind,
    AQUBTransportInbound,
    AQUBTransportRole,
    AQUBTransportStore,
)
from app.application.telegram_processing import canonical_telegram_payload_digest

AQUB_GATE_ENVELOPE_SCHEMA = "aqub.gate-envelope.v1"
AQUB_DELIVERY_COMMAND_SCHEMA = "aqub.delivery-command.v1"
AQUB_PROGRAM_PROMPT_TEXT = (
    "AQUB: пришлите программу следующим сообщением. Достаточно минимум двух "
    "нумерованных шагов (до 3500 символов)."
)
AQUB_RUNTIME_INBOUND_PATH = "/gate/inbound"
AQUB_GATE_DELIVERY_PATH = "/internal/aqub/deliver"

_CANDIDATE_COMMAND_RE = re.compile(
    r"^(?:/ace_join_[0-9a-f]{12}(?:@AQubAgentBot)?$|/program(?:@AQubAgentBot)?(?:\s|$)|/confirm_program_\d{6}(?:@AQubAgentBot)?$|/status(?:@AQubAgentBot)?$|/help(?:@AQubAgentBot)?$)",
    re.IGNORECASE,
)
_PROGRAM_STEP_RE = re.compile(r"(?m)^\s*(\d+)\.\s+\S")

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class AQUBLiveForwardResult:
    accepted: bool
    duplicate: bool
    reason: str
    downstream: JsonObject | None = None
    captured_content: AQUBTransportContentKind | None = None


class AQUBRuntimeUnavailable(RuntimeError):
    """The durable Gate capture succeeded but AQUB runtime was unavailable."""


class AQUBRuntimeClient(Protocol):
    async def post_envelope(
        self,
        *,
        raw_body: bytes,
        signature: str,
    ) -> JsonObject: ...


class HttpAQUBRuntimeClient:
    """AQUB runtime transport backed by the process-owned HTTP connection pool."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    async def post_envelope(self, *, raw_body: bytes, signature: str) -> JsonObject:
        try:
            response = await self._http_client.post(
                f"{self._base_url}{AQUB_RUNTIME_INBOUND_PATH}",
                content=raw_body,
                headers={
                    "content-type": "application/json",
                    "x-aqub-signature": signature,
                },
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise AQUBRuntimeUnavailable("aqub_runtime_unavailable") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise AQUBRuntimeUnavailable("aqub_runtime_unavailable")
        try:
            value: Any = response.json()
        except ValueError as exc:
            raise AQUBRuntimeUnavailable("aqub_runtime_unavailable") from exc
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise AQUBRuntimeUnavailable("aqub_runtime_unavailable")
        return {str(key): item for key, item in value.items()}


class AQUBLiveIngressAdapter:
    """Commit an owned Telegram update in Gate, then forward a redacted signed envelope."""

    def __init__(
        self,
        *,
        binding: AQUBTransportBinding,
        store: AQUBTransportStore,
        runtime: AQUBRuntimeClient | None,
        media_queue: AQUBFoodMediaQueue | None = None,
    ) -> None:
        self._binding = binding
        self._store = store
        self._runtime = runtime
        self._media_queue = media_queue

    async def handle(self, inbound: AQUBTransportInbound) -> AQUBLiveForwardResult:
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
            AQUBTransportCommand(
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
                content_kind=inbound.content_kind,
                photo=inbound.photo,
                voice=inbound.voice,
            )
        )
        _validate_acceptance(acceptance)

        duplicate = acceptance.disposition is AQUBStoreDisposition.DUPLICATE
        media_result: JsonObject | None = None
        if (
            inbound.content_kind in {AQUBTransportContentKind.PHOTO, AQUBTransportContentKind.VOICE}
            and self._media_queue is not None
        ):
            await self._media_queue.enqueue(
                receipt_id=acceptance.command_receipt_id,
                inbound=inbound,
                now=inbound.received_at,
            )
            return AQUBLiveForwardResult(
                accepted=True,
                duplicate=duplicate,
                reason="aqub_food_media_queued",
                downstream={
                    "result": {
                        "kind": "food_media_queued",
                        "reply_text": "Спасибо, файл принят. Разбираюсь с ним.",
                    }
                },
                captured_content=inbound.content_kind,
            )
        raw_body = _build_signed_envelope_body(
            binding=self._binding,
            inbound=inbound,
            role=role,
            command_receipt_id=acceptance.command_receipt_id,
            payload_sha256=payload_sha256,
            media_result=media_result,
        )
        signature = hmac.new(
            self._binding.receipt_signing_key,
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        captured_content = None
        if role is AQUBTransportRole.ALPHA:
            if (
                inbound.content_kind is AQUBTransportContentKind.TEXT
                and isinstance(inbound.command, str)
                and _plain_capture_text(inbound.command)
            ):
                captured_content = AQUBTransportContentKind.TEXT
            elif inbound.content_kind in {
                AQUBTransportContentKind.PHOTO,
                AQUBTransportContentKind.VOICE,
            }:
                captured_content = inbound.content_kind
        try:
            if self._runtime is None:
                raise AQUBRuntimeUnavailable("aqub_runtime_unavailable")
            downstream = await self._runtime.post_envelope(
                raw_body=raw_body,
                signature=signature,
            )
        except AQUBRuntimeUnavailable:
            if captured_content is None:
                raise
            return AQUBLiveForwardResult(
                accepted=True,
                duplicate=duplicate,
                reason="aqub_live_durable_only",
                captured_content=captured_content,
            )
        return AQUBLiveForwardResult(
            accepted=True,
            duplicate=duplicate,
            reason="aqub_live_committed",
            downstream=downstream,
            captured_content=captured_content,
        )


async def forward_aqub_media_result(
    *,
    binding: AQUBTransportBinding,
    inbound: AQUBTransportInbound,
    command_receipt_id: str,
    media_result: Mapping[str, Any],
    runtime: AQUBRuntimeClient,
) -> JsonObject:
    """Forward one durable media result through the exact signed AQUB envelope."""

    role = _bound_role(binding, inbound)
    if role is not AQUBTransportRole.ALPHA:
        raise AQUBRuntimeUnavailable("aqub_media_owner_binding_invalid")
    raw_body = _build_signed_envelope_body(
        binding=binding,
        inbound=inbound,
        role=role,
        command_receipt_id=command_receipt_id,
        payload_sha256=canonical_telegram_payload_digest(dict(inbound.payload)),
        media_result=media_result,
    )
    signature = hmac.new(binding.receipt_signing_key, raw_body, hashlib.sha256).hexdigest()
    return await runtime.post_envelope(raw_body=raw_body, signature=signature)


def _plain_capture_text(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("/")


def _validate_acceptance(acceptance: AQUBDurableAcceptance) -> None:
    if acceptance.status is not AQUBReceiptStatus.COMMITTED:
        raise RuntimeError("aqub_store_not_committed")
    if not isinstance(acceptance.disposition, AQUBStoreDisposition):
        raise RuntimeError("aqub_store_disposition_invalid")
    for value in (
        acceptance.command_receipt_id,
        acceptance.root_event_id,
        acceptance.correlation_id,
        acceptance.causation_id,
    ):
        if not value or len(value) > 1_024:
            raise RuntimeError("aqub_store_reference_invalid")


def _program_like_text(text: str) -> bool:
    positions = [int(match.group(1)) for match in _PROGRAM_STEP_RE.finditer(text)]
    if len(positions) < 2 or positions[0] != 1:
        return False
    return all(position == index for index, position in enumerate(positions, start=1))


def _candidate_input_allowed(inbound: AQUBTransportInbound) -> bool:
    if inbound.content_kind is not AQUBTransportContentKind.TEXT or not isinstance(
        inbound.command, str
    ):
        return False
    text = inbound.command.strip()
    if _CANDIDATE_COMMAND_RE.match(text) is not None:
        return True
    if _reply_context(inbound.payload) == "program_prompt":
        return True
    return _program_like_text(text)


def _bound_role(
    binding: AQUBTransportBinding,
    inbound: AQUBTransportInbound,
) -> AQUBTransportRole | None:
    if (
        not binding.enabled
        or inbound.bot_username != AQUB_BOT_USERNAME
        or inbound.bot_username != binding.bot_username
    ):
        return None
    if inbound.actor_id == binding.alpha_user_id:
        return AQUBTransportRole.ALPHA
    if inbound.chat_id != binding.group_chat_id:
        return None
    if inbound.content_kind is not AQUBTransportContentKind.TEXT:
        return None
    if inbound.actor_id == binding.ace_user_id and binding.ace_user_id != "__unbound_ace__":
        return AQUBTransportRole.ACE
    if _candidate_input_allowed(inbound):
        return AQUBTransportRole.CANDIDATE
    return None


def _validate_time(binding: AQUBTransportBinding, inbound: AQUBTransportInbound) -> None:
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
    binding: AQUBTransportBinding,
    inbound: AQUBTransportInbound,
    role: AQUBTransportRole,
    command_receipt_id: str,
    payload_sha256: str,
    media_result: Mapping[str, Any] | None = None,
) -> bytes:
    message = inbound.payload.get("message")
    if not isinstance(message, Mapping) or type(message.get("message_id")) is not int:
        raise RuntimeError("aqub_message_id_invalid")
    reply = message.get("reply_to_message")
    reply_message_id: int | None = None
    if isinstance(reply, Mapping) and type(reply.get("message_id")) is int:
        reply_message_id = reply["message_id"]

    text = inbound.command if isinstance(inbound.command, str) else ""
    if inbound.content_kind is AQUBTransportContentKind.PHOTO:
        caption = message.get("caption")
        text = caption if isinstance(caption, str) else ""
    media: JsonObject | None = None
    if inbound.photo is not None:
        media = {
            "file_ref": _opaque_ref(
                binding.receipt_signing_key,
                "media",
                inbound.photo.file_unique_id,
            ),
            "media_type": "image/jpeg",
            "width": inbound.photo.width,
            "height": inbound.photo.height,
            "file_size": inbound.photo.file_size,
        }
    elif inbound.voice is not None:
        media = {
            "file_ref": _opaque_ref(
                binding.receipt_signing_key,
                "media",
                inbound.voice.file_unique_id,
            ),
            "media_type": inbound.voice.mime_type or "audio/ogg",
            "duration": inbound.voice.duration,
            "file_size": inbound.voice.file_size,
        }

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
        "content_kind": inbound.content_kind.value,
        "text": text,
        "media": media,
        "media_result": dict(media_result) if media_result is not None else None,
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
