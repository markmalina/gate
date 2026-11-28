"""Exact shared reservation contract across Business and owner-local MTProto."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PhysicalTelegramPeer:
    """Canonical Telegram destination shared by Business and MTProto.

    ``self`` is an MTProto selector, not a distinct Telegram destination.  It
    therefore canonicalizes to the owner's user peer.  Keeping the peer kind in
    the key prevents equal numeric user/chat/channel IDs from being merged.
    """

    kind: str
    provider_id: int

    @classmethod
    def from_identity(cls, kind: str, provider_id: int) -> PhysicalTelegramPeer:
        return cls("user" if kind == "self" else kind, provider_id).validated()

    @classmethod
    def from_key(cls, value: str) -> PhysicalTelegramPeer:
        try:
            kind, raw_id = value.split(":", 1)
            peer = cls(kind, int(raw_id))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("personal_telegram_physical_peer_invalid") from None
        if peer.key != value:
            raise ValueError("personal_telegram_physical_peer_invalid")
        return peer

    def validated(self) -> PhysicalTelegramPeer:
        if self.kind not in {"user", "chat", "channel"}:
            raise ValueError("personal_telegram_physical_peer_invalid")
        if type(self.provider_id) is not int or not 0 < self.provider_id < 2**63:
            raise ValueError("personal_telegram_physical_peer_invalid")
        return self

    @property
    def key(self) -> str:
        self.validated()
        return f"{self.kind}:{self.provider_id}"


@dataclass(frozen=True, slots=True)
class PersonalSendFenceCommand:
    draft_id: UUID | None
    account_id: UUID
    physical_peer_key: str
    actor_ref: str
    session_ref: str
    peer_ref: str
    exact_text_digest: str
    approval_digest: str
    idempotency_key: str
    now: datetime


@dataclass(frozen=True, slots=True)
class PersonalSendFenceResult:
    fence_id: UUID
    outcome: str
    receipt_ref: str | None
    replay: bool


class PersonalSendFencePort(Protocol):
    """Implementations commit the reservation before permitting provider I/O."""

    async def reserve(self, command: PersonalSendFenceCommand) -> PersonalSendFenceResult: ...

    async def finalize(
        self,
        command: PersonalSendFenceCommand,
        *,
        outcome: str,
        receipt_ref: str | None,
        external_effect_performed: bool | None,
    ) -> PersonalSendFenceResult: ...
