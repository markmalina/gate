"""Offline Telegram Desktop JSON reader with privacy-bounded output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.contact_sync.models import ContactReadResult, ContactSnapshotItem


class TelegramDesktopExportError(Exception):
    """Safe export validation error that never includes private file values."""


class TelegramDesktopExportReader:
    """Extract only account ownership and user-id/name pairs from a Desktop export."""

    MAX_FILE_BYTES = 100_000_000

    def __init__(self, export_file: Path) -> None:
        self._export_file = export_file

    async def read_contacts(self) -> ContactReadResult:
        payload = self._read_payload()
        owner_user_id = _positive_id(
            _mapping(payload.get("personal_information")).get("user_id")
        )
        if owner_user_id is None:
            raise TelegramDesktopExportError(
                "Telegram Desktop export does not contain a valid account user id"
            )

        exported_contacts = _items(_mapping(payload.get("contacts")).get("list"))
        by_user_id: dict[str, ContactSnapshotItem] = {}

        frequent_contacts = _items(
            _mapping(payload.get("frequent_contacts")).get("list")
        )
        for item in frequent_contacts:
            if item.get("type") != "user":
                continue
            _add_contact(by_user_id, item.get("id"), item.get("name"))

        chats = _items(_mapping(payload.get("chats")).get("list"))
        for item in chats:
            if item.get("type") != "personal_chat":
                continue
            _add_contact(by_user_id, item.get("id"), item.get("name"))

        direct_contacts = tuple(
            sorted(by_user_id.values(), key=lambda contact: int(contact.telegram_user_id))
        )
        if not direct_contacts:
            raise TelegramDesktopExportError(
                "Telegram Desktop export contains no usable personal contact identities"
            )

        return ContactReadResult(
            saved_contacts=(),
            telegram_users=(),
            direct_contacts=direct_contacts,
            source_contacts=max(len(exported_contacts), len(direct_contacts)),
            source="telegram_desktop_export",
            source_account_telegram_user_id=owner_user_id,
        )

    def _read_payload(self) -> dict[str, Any]:
        try:
            if self._export_file.stat().st_size > self.MAX_FILE_BYTES:
                raise TelegramDesktopExportError(
                    "Telegram Desktop export exceeds the 100 MB safety limit"
                )
            with self._export_file.open(encoding="utf-8") as stream:
                payload = json.load(stream)
        except TelegramDesktopExportError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TelegramDesktopExportError(
                "Telegram Desktop export could not be read as JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise TelegramDesktopExportError(
                "Telegram Desktop export root must be a JSON object"
            )
        return payload


def _add_contact(
    contacts: dict[str, ContactSnapshotItem],
    raw_user_id: object,
    raw_name: object,
) -> None:
    user_id = _positive_id(raw_user_id)
    name = _text(raw_name)[:255]
    if user_id is None or not name:
        return
    contacts[user_id] = ContactSnapshotItem(
        telegram_user_id=user_id,
        display_name=name,
    )


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _items(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_mapping(item) for item in value if isinstance(item, dict))


def _positive_id(value: object) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return str(value)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
