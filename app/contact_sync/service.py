"""Deterministic orchestration for Telegram contact snapshot synchronization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from app.contact_sync.models import (
    ContactReader,
    ContactSnapshotItem,
    ContactSyncResult,
    SavedPhoneContact,
    SnapshotImportCounts,
    SnapshotPublisher,
    TelegramContactUser,
)


class ContactSyncError(Exception):
    """Expected synchronization error safe to show without contact data."""


class ContactSyncService:
    """Match saved labels to Telegram identities and publish bounded chunks."""

    MAX_CHUNK_SIZE = 1000

    def __init__(self, reader: ContactReader, publisher: SnapshotPublisher) -> None:
        self._reader = reader
        self._publisher = publisher

    async def execute(self, *, account_alias: str) -> ContactSyncResult:
        normalized_alias = account_alias.strip()
        if not normalized_alias or len(normalized_alias) > 128:
            raise ContactSyncError("Account alias must contain 1 to 128 characters")

        source = await self._reader.read_contacts()
        contacts = build_snapshot(source.saved_contacts, source.telegram_users)
        chunks = tuple(_chunked(contacts, self.MAX_CHUNK_SIZE))
        totals = SnapshotImportCounts(received=0, created=0, updated=0, unchanged=0)

        if chunks:
            snapshot_digest = _snapshot_digest(contacts)
            chunk_total = len(chunks)
            for chunk_index, chunk in enumerate(chunks, start=1):
                counts = await self._publisher.publish(
                    account_alias=normalized_alias,
                    revision=_chunk_revision(snapshot_digest, chunk_index, chunk_total),
                    contacts=chunk,
                )
                if counts.received != len(chunk):
                    raise ContactSyncError("Gate returned inconsistent aggregate import counts")
                totals = SnapshotImportCounts(
                    received=totals.received + counts.received,
                    created=totals.created + counts.created,
                    updated=totals.updated + counts.updated,
                    unchanged=totals.unchanged + counts.unchanged,
                )

        saved_count = len(source.saved_contacts)
        matched_count = len(contacts)
        return ContactSyncResult(
            account_alias=normalized_alias,
            saved_contacts=saved_count,
            matched_contacts=matched_count,
            skipped_contacts=max(saved_count - matched_count, 0),
            chunks=len(chunks),
            import_counts=totals,
        )


def build_snapshot(
    saved_contacts: Iterable[SavedPhoneContact],
    telegram_users: Iterable[TelegramContactUser],
) -> tuple[ContactSnapshotItem, ...]:
    """Join private saved labels to Telegram user IDs by normalized phone number."""

    saved_by_phone: dict[str, SavedPhoneContact] = {}
    for contact in saved_contacts:
        phone = _normalize_phone(contact.phone)
        display_name = _display_name(contact.first_name, contact.last_name)
        if not phone or not display_name:
            continue
        current = saved_by_phone.get(phone)
        if current is None or _saved_contact_order(contact) > _saved_contact_order(current):
            saved_by_phone[phone] = contact

    by_user_id: dict[str, ContactSnapshotItem] = {}
    for user in telegram_users:
        user_id = user.telegram_user_id.strip()
        saved = saved_by_phone.get(_normalize_phone(user.phone))
        if saved is None or not user_id.isdecimal():
            continue
        display_name = _display_name(saved.first_name, saved.last_name)
        if not display_name:
            continue
        username = _normalize_username(user.username)
        by_user_id[user_id] = ContactSnapshotItem(
            telegram_user_id=user_id,
            display_name=display_name,
            username=username,
        )

    return tuple(
        sorted(by_user_id.values(), key=lambda contact: int(contact.telegram_user_id))
    )


def _normalize_phone(value: str) -> str:
    return "".join(character for character in value if character.isdecimal())


def _display_name(first_name: str, last_name: str) -> str:
    return " ".join(part.strip() for part in (first_name, last_name) if part.strip())[:255]


def _normalize_username(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().removeprefix("@").strip()
    return normalized[:255] or None


def _saved_contact_order(contact: SavedPhoneContact) -> tuple[int, str, str]:
    return contact.saved_at, contact.first_name, contact.last_name


def _snapshot_digest(contacts: tuple[ContactSnapshotItem, ...]) -> str:
    serialized = json.dumps(
        [
            {
                "telegram_user_id": contact.telegram_user_id,
                "display_name": contact.display_name,
                "username": contact.username,
            }
            for contact in contacts
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _chunk_revision(digest: str, index: int, total: int) -> str:
    return f"telegram-contact-v1-{digest[:32]}-{index:04d}-{total:04d}"


def _chunked(
    contacts: tuple[ContactSnapshotItem, ...], chunk_size: int
) -> Iterable[tuple[ContactSnapshotItem, ...]]:
    for offset in range(0, len(contacts), chunk_size):
        yield contacts[offset : offset + chunk_size]
