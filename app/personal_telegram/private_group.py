"""Owner-local one-attempt private supergroup creation; disabled by default.

The session and group pin stay on the original DPAPI profile. A provider
timeout is ambiguous and never becomes an automatic create retry.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.application.personal_telegram_transport import (
    PersonalTelegramPolicy,
    PersonalTelegramTransportError,
    _digest,
    _require_prefixed_ref,
    _secret_digest,
)
from app.personal_telegram.enrollment import (
    PersonalTelegramEnrollmentError,
    read_encryption_key_file,
)
from app.personal_telegram.local_runtime import LocalEncryptedSessionSource


class GroupOutcome(StrEnum):
    CREATED = "created"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CreatePrivateGroupCommand:
    actor_ref: str
    session_ref: str
    exact_title: str
    approval_token: str
    idempotency_key: str
    now: datetime


@dataclass(frozen=True, slots=True)
class PrivateGroupReceipt:
    outcome: GroupOutcome
    channel_id: int | None = None
    access_hash: int | None = None
    owner_verified: bool = False
    private_verified: bool = False


@dataclass(frozen=True, slots=True)
class PrivateGroupResult:
    outcome: GroupOutcome
    group_ref: str | None
    idempotent_replay: bool
    owner_verified: bool
    private_verified: bool


class PrivateGroupTransport(Protocol):
    async def create(self, *, session_ref: str, exact_title: str) -> PrivateGroupReceipt: ...


def _title_digest(title: str) -> str:
    if not isinstance(title, str) or not 1 <= len(title) <= 128 or title != title.strip():
        raise PersonalTelegramTransportError("personal_telegram_group_title_invalid")
    return _digest({"exact_title": title})


def _fingerprint(command: CreatePrivateGroupCommand) -> str:
    return _digest(
        {
            "actor_ref": _require_prefixed_ref(command.actor_ref, "actor_", "actor_ref"),
            "session_ref": _require_prefixed_ref(command.session_ref, "session_", "session_ref"),
            "title_digest": _title_digest(command.exact_title),
            "approval_digest": _secret_digest(command.approval_token),
            "idempotency_key": _require_prefixed_ref(
                command.idempotency_key, "group_", "idempotency_key"
            ),
        }
    )


class SqlitePrivateGroupJournal:
    """Owner-only encrypted pin store and durable no-retry creation ledger."""

    def __init__(self, path: Path, *, key_file: Path) -> None:
        if not path.is_absolute() or path.is_symlink() or (path.exists() and not path.is_file()):
            raise PersonalTelegramTransportError("personal_telegram_group_journal_path_invalid")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if key_file.is_symlink() or not key_file.is_file():
            raise PersonalTelegramTransportError("personal_telegram_group_key_unavailable")
        if os.name != "nt" and (
            path.parent.stat().st_mode & 0o077 or key_file.stat().st_mode & 0o077
        ):
            raise PersonalTelegramTransportError("personal_telegram_group_permissions_invalid")
        self._path = path
        try:
            self._fernet = Fernet(read_encryption_key_file(key_file))
        except (OSError, ValueError, TypeError, PersonalTelegramEnrollmentError):
            raise PersonalTelegramTransportError(
                "personal_telegram_group_key_unavailable"
            ) from None
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_approvals (
                    token_digest TEXT PRIMARY KEY,
                    actor_ref TEXT NOT NULL,
                    session_ref TEXT NOT NULL,
                    title_digest TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS group_attempts (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (outcome IN ('unknown', 'created', 'rejected')),
                    group_ref TEXT UNIQUE,
                    encrypted_pin BLOB,
                    owner_verified INTEGER NOT NULL DEFAULT 0,
                    private_verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._path.is_symlink():
            raise PersonalTelegramTransportError("personal_telegram_group_journal_path_invalid")
        descriptor = os.open(
            self._path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        os.close(descriptor)
        if os.name != "nt" and self._path.stat().st_mode & 0o077:
            raise PersonalTelegramTransportError("personal_telegram_group_permissions_invalid")
        db = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def register_exact_approval(
        self,
        *,
        approval_token: str,
        actor_ref: str,
        session_ref: str,
        exact_title: str,
        expires_at: datetime,
    ) -> None:
        if expires_at.tzinfo is None:
            raise PersonalTelegramTransportError("personal_telegram_group_expiry_invalid")
        with self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO group_approvals VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        _secret_digest(approval_token),
                        _require_prefixed_ref(actor_ref, "actor_", "actor_ref"),
                        _require_prefixed_ref(session_ref, "session_", "session_ref"),
                        _title_digest(exact_title),
                        expires_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise PersonalTelegramTransportError(
                    "personal_telegram_group_approval_duplicate"
                ) from None

    def revoke_approval(self, approval_token: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE group_approvals SET consumed = 1 WHERE token_digest = ? AND consumed = 0",
                (_secret_digest(approval_token),),
            )
            return result.rowcount == 1

    def reserve(self, command: CreatePrivateGroupCommand) -> PrivateGroupResult:
        if command.now.tzinfo is None:
            raise PersonalTelegramTransportError("personal_telegram_group_time_invalid")
        fingerprint = _fingerprint(command)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM group_attempts WHERE idempotency_key = ?",
                (command.idempotency_key,),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise PersonalTelegramTransportError(
                        "personal_telegram_group_idempotency_conflict"
                    )
                return _result(previous, replay=True)
            token_digest = _secret_digest(command.approval_token)
            approval = db.execute(
                "SELECT * FROM group_approvals WHERE token_digest = ?", (token_digest,)
            ).fetchone()
            if approval is None or approval["consumed"]:
                raise PersonalTelegramTransportError("personal_telegram_group_approval_unavailable")
            if datetime.fromisoformat(approval["expires_at"]) <= command.now:
                raise PersonalTelegramTransportError("personal_telegram_group_approval_expired")
            if (
                approval["actor_ref"] != command.actor_ref
                or approval["session_ref"] != command.session_ref
                or approval["title_digest"] != _title_digest(command.exact_title)
            ):
                raise PersonalTelegramTransportError("personal_telegram_group_approval_mismatch")
            db.execute(
                "INSERT INTO group_attempts "
                "(idempotency_key, fingerprint, outcome, created_at) "
                "VALUES (?, ?, 'unknown', ?)",
                (command.idempotency_key, fingerprint, command.now.isoformat()),
            )
            db.execute(
                "UPDATE group_approvals SET consumed = 1 WHERE token_digest = ?", (token_digest,)
            )
            db.commit()  # The only permitted provider attempt comes after this commit.
        return PrivateGroupResult(GroupOutcome.UNKNOWN, None, False, False, False)

    def finalize(
        self, command: CreatePrivateGroupCommand, receipt: PrivateGroupReceipt
    ) -> PrivateGroupResult:
        fingerprint = _fingerprint(command)
        valid_created = (
            receipt.outcome is GroupOutcome.CREATED
            and receipt.owner_verified
            and receipt.private_verified
            and type(receipt.channel_id) is int
            and receipt.channel_id > 0
            and type(receipt.access_hash) is int
            and receipt.access_hash != 0
        )
        outcome = (
            GroupOutcome.CREATED
            if valid_created
            else GroupOutcome.REJECTED
            if receipt.outcome is GroupOutcome.REJECTED
            else GroupOutcome.UNKNOWN
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM group_attempts WHERE idempotency_key = ?",
                (command.idempotency_key,),
            ).fetchone()
            if row is None or row["fingerprint"] != fingerprint:
                raise PersonalTelegramTransportError("personal_telegram_group_attempt_conflict")
            if row["outcome"] != "unknown" or row["group_ref"] is not None:
                return _result(row, replay=True)
            group_ref = f"group_{uuid4().hex}" if valid_created else None
            encrypted_pin = (
                self._fernet.encrypt(
                    json.dumps(
                        {"id": receipt.channel_id, "access_hash": receipt.access_hash}
                    ).encode()
                )
                if valid_created
                else None
            )
            db.execute(
                "UPDATE group_attempts SET outcome = ?, group_ref = ?, encrypted_pin = ?, "
                "owner_verified = ?, private_verified = ? WHERE idempotency_key = ?",
                (
                    outcome.value,
                    group_ref,
                    encrypted_pin,
                    int(valid_created),
                    int(valid_created),
                    command.idempotency_key,
                ),
            )
            db.commit()
            updated = db.execute(
                "SELECT * FROM group_attempts WHERE idempotency_key = ?",
                (command.idempotency_key,),
            ).fetchone()
            assert updated is not None
            return _result(updated, replay=False)

    def resolve_pin(self, group_ref: str) -> tuple[int, int]:
        _require_prefixed_ref(group_ref, "group_", "group_ref")
        with self._connect() as db:
            row = db.execute(
                "SELECT encrypted_pin FROM group_attempts "
                "WHERE group_ref = ? AND outcome = 'created'",
                (group_ref,),
            ).fetchone()
        if row is None or row["encrypted_pin"] is None:
            raise PersonalTelegramTransportError("personal_telegram_group_pin_unavailable")
        try:
            pin = json.loads(self._fernet.decrypt(row["encrypted_pin"]))
            if type(pin["id"]) is not int or type(pin["access_hash"]) is not int:
                raise ValueError("pin_invalid")
            return pin["id"], pin["access_hash"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise PersonalTelegramTransportError(
                "personal_telegram_group_pin_unavailable"
            ) from None


def _result(row: sqlite3.Row, *, replay: bool) -> PrivateGroupResult:
    return PrivateGroupResult(
        GroupOutcome(row["outcome"]),
        row["group_ref"],
        replay,
        bool(row["owner_verified"]),
        bool(row["private_verified"]),
    )


class TelethonPrivateGroupTransport:
    """One raw CreateChannel request with no participants or public username."""

    def __init__(
        self,
        *,
        source: LocalEncryptedSessionSource,
        api_id: int,
        api_hash: str,
        expected_owner_user_id: int,
    ) -> None:
        if (
            type(api_id) is not int
            or api_id <= 0
            or not api_hash
            or type(expected_owner_user_id) is not int
            or expected_owner_user_id <= 0
        ):
            raise PersonalTelegramTransportError("personal_telegram_group_binding_invalid")
        self._source = source
        self._api_id = api_id
        self._api_hash = api_hash
        self._expected_owner_user_id = expected_owner_user_id

    async def create(self, *, session_ref: str, exact_title: str) -> PrivateGroupReceipt:
        _require_prefixed_ref(session_ref, "session_", "session_ref")
        _title_digest(exact_title)
        session = self._source.load(session_ref=session_ref)
        try:
            from telethon import TelegramClient, functions  # type: ignore[import-untyped]
            from telethon.sessions import StringSession  # type: ignore[import-untyped]
        except ImportError:
            raise PersonalTelegramTransportError(
                "personal_telegram_telethon_not_installed"
            ) from None
        client: Any = TelegramClient(
            StringSession(session),
            self._api_id,
            self._api_hash,
            receive_updates=False,
            request_retries=0,
            connection_retries=0,
            auto_reconnect=False,
            flood_sleep_threshold=0,
            raise_last_call_error=True,
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return PrivateGroupReceipt(GroupOutcome.REJECTED)
            owner = await client.get_me()
            if owner is None or owner.id != self._expected_owner_user_id:
                return PrivateGroupReceipt(GroupOutcome.REJECTED)
            response = await asyncio.wait_for(
                client(
                    functions.channels.CreateChannelRequest(
                        title=exact_title,
                        about="",
                        megagroup=True,
                        broadcast=False,
                    )
                ),
                timeout=15,
            )
            channels = tuple(getattr(response, "chats", ()))
            if len(channels) != 1:
                return PrivateGroupReceipt(GroupOutcome.UNKNOWN)
            channel = channels[0]
            private = (
                getattr(channel, "megagroup", False) is True
                and getattr(channel, "broadcast", True) is False
                and not getattr(channel, "username", None)
                and not getattr(channel, "usernames", None)
                and getattr(channel, "title", None) == exact_title
                and getattr(channel, "participants_count", None) in (None, 1)
            )
            creator = getattr(channel, "creator", False) is True
            if not private or not creator:
                return PrivateGroupReceipt(GroupOutcome.UNKNOWN)
            return PrivateGroupReceipt(
                GroupOutcome.CREATED,
                channel_id=getattr(channel, "id", None),
                access_hash=getattr(channel, "access_hash", None),
                owner_verified=True,
                private_verified=True,
            )
        except Exception:
            return PrivateGroupReceipt(GroupOutcome.UNKNOWN)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


class CreatePrivateGroupLocalService:
    def __init__(
        self,
        *,
        policy: PersonalTelegramPolicy,
        journal: SqlitePrivateGroupJournal,
        transport: PrivateGroupTransport,
    ) -> None:
        self._policy = policy
        self._journal = journal
        self._transport = transport

    async def execute(self, command: CreatePrivateGroupCommand) -> PrivateGroupResult:
        if not self._policy.create_group_enabled:
            raise PersonalTelegramTransportError("personal_telegram_group_disabled")
        reservation = self._journal.reserve(command)
        if reservation.idempotent_replay:
            return reservation
        try:
            receipt = await self._transport.create(
                session_ref=command.session_ref, exact_title=command.exact_title
            )
        except Exception:
            receipt = PrivateGroupReceipt(GroupOutcome.UNKNOWN)
        return self._journal.finalize(command, receipt)
