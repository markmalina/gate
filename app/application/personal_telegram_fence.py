"""Exact shared reservation contract across Business and owner-local MTProto."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PersonalSendFenceCommand:
    draft_id: UUID
    account_id: UUID
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
