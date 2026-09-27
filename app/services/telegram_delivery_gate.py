"""Process registry of durable bot rate gates and scoped, already-reserved sends."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol


class TelegramDeliveryGate(Protocol):
    async def before(self, payload: dict[str, Any]) -> float: ...
    async def failed(self, payload: dict[str, Any], retry_after: int | None) -> None: ...


_gates: dict[str, TelegramDeliveryGate] = {}
_reserved: ContextVar[str | None] = ContextVar("telegram_reserved_bot", default=None)


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
    if _reserved.get() == prefix:
        return None
    gate = _gates.get(prefix)
    if gate is None:
        from app.core.config import get_settings

        settings = get_settings()
        if (
            settings.gate_reply_sequence_stop_on_inbound_enabled
            and prefix == settings.telegram_bot_token.split(":", 1)[0]
        ):
            raise RuntimeError("Telegram shared budget is not initialized")
    return gate


@contextmanager
def reserved_delivery(credential: str) -> Iterator[None]:
    token = _reserved.set(credential.split(":", 1)[0])
    try:
        yield
    finally:
        _reserved.reset(token)
