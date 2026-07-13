"""Application contract for bounded Telegram contact snapshot imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ContactSnapshotError(Exception):
    """Expected contact snapshot error safe to expose through the admin API."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ContactSnapshotContact:
    """One sanitized account-scoped Telegram contact."""

    telegram_user_id: str
    display_name: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class ImportContactSnapshotCommand:
    """A bounded snapshot import for one existing Gate account."""

    account_alias: str
    revision: str
    contacts: tuple[ContactSnapshotContact, ...]
    source: str = "telegram_user_session"
    source_account_telegram_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContactSnapshotCounts:
    """Aggregate import counts safe to return and audit."""

    received: int
    created: int
    updated: int
    unchanged: int


@dataclass(frozen=True, slots=True)
class ContactSnapshotResult:
    """Sanitized result that never contains contact data."""

    account_alias: str
    revision_sha256: str
    counts: ContactSnapshotCounts


class ContactSnapshotWriter(Protocol):
    """Persistence port for one atomic snapshot upsert."""

    async def write(self, command: ImportContactSnapshotCommand) -> ContactSnapshotResult: ...


class ImportContactSnapshotService:
    """Normalize and validate a contact snapshot before persistence."""

    MAX_CONTACTS = 1000
    MAX_ACCOUNT_ALIAS_LENGTH = 128
    MAX_REVISION_LENGTH = 128
    MAX_ID_LENGTH = 255
    MAX_NAME_LENGTH = 255
    MAX_USERNAME_LENGTH = 255
    ALLOWED_SOURCES = {"telegram_user_session", "telegram_desktop_export"}

    def __init__(self, writer: ContactSnapshotWriter) -> None:
        self._writer = writer

    async def execute(
        self,
        command: ImportContactSnapshotCommand,
    ) -> ContactSnapshotResult:
        account_alias = command.account_alias.strip()
        revision = command.revision.strip()
        source = command.source.strip()
        source_account_user_id = (
            command.source_account_telegram_user_id.strip()
            if command.source_account_telegram_user_id is not None
            else None
        )
        if not account_alias or len(account_alias) > self.MAX_ACCOUNT_ALIAS_LENGTH:
            raise ContactSnapshotError(
                code="invalid_account_alias",
                message="Account alias must contain 1 to 128 characters",
            )
        if not revision or len(revision) > self.MAX_REVISION_LENGTH:
            raise ContactSnapshotError(
                code="invalid_revision",
                message="Revision must contain 1 to 128 characters",
            )
        if not command.contacts or len(command.contacts) > self.MAX_CONTACTS:
            raise ContactSnapshotError(
                code="invalid_contacts",
                message="Contact snapshot must contain 1 to 1000 contacts",
            )
        if source not in self.ALLOWED_SOURCES:
            raise ContactSnapshotError(
                code="invalid_source",
                message="Contact snapshot source is not supported",
            )
        if source_account_user_id is not None and (
            not source_account_user_id.isdecimal()
            or len(source_account_user_id) > self.MAX_ID_LENGTH
        ):
            raise ContactSnapshotError(
                code="invalid_source_account_telegram_user_id",
                message="Source account Telegram user id must contain decimal digits only",
            )
        if source == "telegram_desktop_export" and source_account_user_id is None:
            raise ContactSnapshotError(
                code="source_account_identity_required",
                message="Desktop exports require a source account Telegram user id",
            )

        normalized: list[ContactSnapshotContact] = []
        seen_ids: set[str] = set()
        for contact in command.contacts:
            telegram_user_id = contact.telegram_user_id.strip()
            display_name = contact.display_name.strip()
            username = self._normalize_username(contact.username)
            if (
                not telegram_user_id
                or len(telegram_user_id) > self.MAX_ID_LENGTH
                or not telegram_user_id.isdecimal()
            ):
                raise ContactSnapshotError(
                    code="invalid_telegram_user_id",
                    message="Telegram user id must contain decimal digits only",
                )
            if not display_name or len(display_name) > self.MAX_NAME_LENGTH:
                raise ContactSnapshotError(
                    code="invalid_display_name",
                    message="Display name must contain 1 to 255 characters",
                )
            if username is not None and len(username) > self.MAX_USERNAME_LENGTH:
                raise ContactSnapshotError(
                    code="invalid_username",
                    message="Username must not exceed 255 characters",
                )
            if telegram_user_id in seen_ids:
                raise ContactSnapshotError(
                    code="duplicate_telegram_user_id",
                    message="Telegram user ids must be unique inside one snapshot",
                )
            seen_ids.add(telegram_user_id)
            normalized.append(
                ContactSnapshotContact(
                    telegram_user_id=telegram_user_id,
                    display_name=display_name,
                    username=username,
                )
            )

        return await self._writer.write(
            ImportContactSnapshotCommand(
                account_alias=account_alias,
                revision=revision,
                contacts=tuple(normalized),
                source=source,
                source_account_telegram_user_id=source_account_user_id,
            )
        )

    @staticmethod
    def _normalize_username(username: str | None) -> str | None:
        if username is None:
            return None
        normalized = username.strip().removeprefix("@").strip()
        return normalized or None
