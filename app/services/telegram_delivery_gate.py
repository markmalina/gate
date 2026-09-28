"""Process registry of durable bot rate gates and scoped, already-reserved sends."""

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


class TelegramDeliveryGate(Protocol):
    async def before(self, payload: dict[str, Any]) -> float: ...
    async def failed(self, payload: dict[str, Any], retry_after: int | None) -> None: ...
    async def reserve_canary(self, intent: "CanaryDelivery", payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class CanaryDelivery:
    """Bound to persisted dispatch/outbox state, then verified against HTTP JSON."""

    kind: str
    principal_subject: str
    account_id: UUID | None
    dialog_ref: str | None
    body_sha256: str | None
    destination: str
    business_connection_id: str | None = None
    notification_template: str | None = None
    notification_text_sha256: str | None = None

    def matches(self, payload: dict[str, Any]) -> bool:
        text = payload.get("text")
        if not isinstance(text, str) or str(payload.get("chat_id")) != self.destination:
            return False
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if self.kind == "sequence":
            return (
                self.body_sha256 == digest
                and self.dialog_ref == self.destination
                and self.business_connection_id is not None
                and payload.get("business_connection_id") == self.business_connection_id
            )
        if self.kind == "notification":
            return (
                self.notification_text_sha256 == digest
                and self.business_connection_id is None
                and "business_connection_id" not in payload
            )
        return False


_gates: dict[str, TelegramDeliveryGate] = {}
_reserved: ContextVar[tuple[str, float] | None] = ContextVar("telegram_reserved_bot", default=None)
_canary: ContextVar[CanaryDelivery | None] = ContextVar("telegram_canary", default=None)


class TelegramDispatchStartExpired(RuntimeError):
    """Proven local refusal before the HTTP adapter is invoked."""


def require_reserved_start_window(credential: str) -> None:
    reservation = _reserved.get()
    if reservation is not None and reservation[0] == credential.split(":", 1)[0]:
        if time.monotonic() > reservation[1]:
            raise TelegramDispatchStartExpired("dispatch_start_window_expired")


def registered_gate(bot_prefix: str) -> TelegramDeliveryGate | None:
    return _gates.get(bot_prefix)


def register_gate(bot_prefix: str, gate: TelegramDeliveryGate) -> None:
    existing = _gates.get(bot_prefix)
    if existing is not None and existing is not gate:
        raise RuntimeError("Telegram bot budget already has a process owner")
    _gates[bot_prefix] = gate


def unregister_gate(bot_prefix: str, gate: TelegramDeliveryGate) -> None:
    if _gates.get(bot_prefix) is gate:
        del _gates[bot_prefix]


def delivery_gate(credential: str) -> TelegramDeliveryGate | None:
    prefix = credential.split(":", 1)[0]
    reservation = _reserved.get()
    if reservation is not None and reservation[0] == prefix:
        return None
    gate = _gates.get(prefix)
    if gate is None:
        from app.core.config import get_settings

        settings = get_settings()
        if (
            (
                settings.gate_reply_sequence_stop_on_inbound_enabled
                or settings.gate_reply_sequence_canary_enabled
            )
            and prefix == settings.telegram_bot_token.split(":", 1)[0]
        ):
            raise RuntimeError("Telegram shared budget is not initialized")
    return gate


def canary_intent() -> CanaryDelivery | None:
    return _canary.get()


@contextmanager
def canary_delivery(intent: CanaryDelivery) -> Iterator[None]:
    token = _canary.set(intent)
    try:
        yield
    finally:
        _canary.reset(token)


@contextmanager
def reserved_delivery(credential: str, *, admitted_at: float) -> Iterator[None]:
    token = _reserved.set((credential.split(":", 1)[0], admitted_at + 0.1))
    try:
        yield
    finally:
        _reserved.reset(token)
