"""Safe data contracts for the read-only contact synchronizer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SavedPhoneContact:
    """Private Telegram address-book label before identity matching."""

    phone: str
    first_name: str
    last_name: str = ""
    saved_at: int = 0


@dataclass(frozen=True, slots=True)
class TelegramContactUser:
    """Telegram contact identity eligible for Gate matching."""

    telegram_user_id: str
    phone: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class ContactSnapshotItem:
    """Sanitized account-scoped contact sent to Gate."""

    telegram_user_id: str
    display_name: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class ContactReadResult:
    """Private reader result; callers must not log its collections."""

    saved_contacts: tuple[SavedPhoneContact, ...]
    telegram_users: tuple[TelegramContactUser, ...]
    direct_contacts: tuple[ContactSnapshotItem, ...] = ()
    source_contacts: int | None = None
    source: str = "telegram_user_session"
    source_account_telegram_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotImportCounts:
    """Aggregate Gate import counts safe for operational output."""

    received: int
    created: int
    updated: int
    unchanged: int


@dataclass(frozen=True, slots=True)
class ContactSyncResult:
    """Privacy-bounded result that contains no contact values."""

    account_alias: str
    source: str
    saved_contacts: int
    matched_contacts: int
    skipped_contacts: int
    chunks: int
    import_counts: SnapshotImportCounts


class ContactReader(Protocol):
    """Port limited to reading the Telegram contact directory."""

    async def read_contacts(self) -> ContactReadResult: ...


class SnapshotPublisher(Protocol):
    """Port for publishing one sanitized bounded snapshot chunk."""

    async def publish(
        self,
        *,
        account_alias: str,
        revision: str,
        source: str,
        source_account_telegram_user_id: str | None,
        contacts: tuple[ContactSnapshotItem, ...],
    ) -> SnapshotImportCounts: ...
