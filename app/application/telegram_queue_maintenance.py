"""Application contracts for redacted Telegram queue health and stale recovery."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class TelegramQueueHealth:
    """Aggregate queue state without payloads or Telegram peer identifiers."""

    pending: int
    processing: int
    retry_scheduled: int
    delivered: int
    dead_letter: int
    stale_processing: int
    oldest_due_at: datetime | None


class TelegramQueueHealthReader(Protocol):
    """Read only aggregate operational queue state."""

    async def read(
        self,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> TelegramQueueHealth:
        """Return aggregate counts and due age without message content."""


@dataclass(frozen=True)
class RecoverStaleTelegramClaimsCommand:
    """Bounded recovery request for abandoned processing leases."""

    stale_before: datetime
    recovered_at: datetime
    max_attempts: int = 5
    limit: int = 100
    require_persisted_voice_transcript: bool = False
    require_missing_voice_archive: bool = False


@dataclass(frozen=True)
class TelegramStaleClaimRecovery:
    """Redacted counts from one bounded recovery transaction."""

    recovered: int
    retry_scheduled: int
    dead_lettered: int


class TelegramQueueMaintenance(Protocol):
    """Recover stale claims without processing Telegram payloads."""

    async def recover_stale(
        self,
        command: RecoverStaleTelegramClaimsCommand,
    ) -> TelegramStaleClaimRecovery:
        """Retry or dead-letter at most the requested number of stale claims."""


class RecoverStaleTelegramClaimsService:
    """Validate recovery bounds before entering the persistence adapter."""

    def __init__(self, maintenance: TelegramQueueMaintenance) -> None:
        self._maintenance = maintenance

    async def execute(
        self,
        command: RecoverStaleTelegramClaimsCommand,
    ) -> TelegramStaleClaimRecovery:
        if command.stale_before >= command.recovered_at:
            raise ValueError("stale_before must be earlier than recovered_at")
        if command.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not 1 <= command.limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if (
            command.require_persisted_voice_transcript
            and command.require_missing_voice_archive
        ):
            raise ValueError("voice recovery predicates are mutually exclusive")
        return await self._maintenance.recover_stale(command)
