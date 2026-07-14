"""Application contract for bounded stale reply-send recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RecoverStaleReplySendsCommand:
    """A bounded request to close reply-send attempts abandoned before an outcome."""

    stale_before: datetime
    recovered_at: datetime
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ReplySendRecoveryResult:
    """Aggregate recovery counts without message, peer, or account identifiers."""

    recovered: int
    sending_marked_unknown: int
    reserved_cancelled: int


class ReplySendRecovery(Protocol):
    """Persistence port for one bounded stale-attempt recovery transaction."""

    async def recover_stale(
        self,
        command: RecoverStaleReplySendsCommand,
    ) -> ReplySendRecoveryResult: ...


class RecoverStaleReplySendsService:
    """Validate recovery bounds before entering the persistence adapter."""

    def __init__(self, recovery: ReplySendRecovery) -> None:
        self._recovery = recovery

    async def execute(
        self,
        command: RecoverStaleReplySendsCommand,
    ) -> ReplySendRecoveryResult:
        if not _is_aware(command.stale_before) or not _is_aware(command.recovered_at):
            raise ValueError("stale_before and recovered_at must be timezone-aware")
        if command.stale_before >= command.recovered_at:
            raise ValueError("stale_before must be earlier than recovered_at")
        if not 1 <= command.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return await self._recovery.recover_stale(command)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
