"""Owner-local, fail-closed send journal for the personal Telegram transport.

The attempt is committed as UNKNOWN before any provider I/O. An interrupted
process can therefore never turn a possible send into an automatic retry.
This store is not the shared Business/MTProto delivery fence.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from app.application.personal_telegram_fence import (
    PersonalSendFenceCommand,
    PersonalSendFencePort,
    PersonalSendFenceResult,
)
from app.application.personal_telegram_transport import (
    ExactSendCommand,
    ExactSendResult,
    PersonalTelegramPolicy,
    PersonalTelegramTransport,
    PersonalTelegramTransportError,
    ProviderOutcome,
    ProviderSendReceipt,
    _deterministic_random_id,
    _digest,
    _exact_text_digest,
    _require_opaque_ref,
    _require_prefixed_ref,
    _secret_digest,
)


@dataclass(frozen=True, slots=True)
class _Reservation:
    result: ExactSendResult
    replay: bool


class SqlitePersonalTelegramSendJournal:
    """One-host durable approvals and attempts, with no text or secret tokens."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise PersonalTelegramTransportError("personal_telegram_journal_path_invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise PersonalTelegramTransportError("personal_telegram_journal_path_invalid")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            if path.parent.stat().st_mode & 0o077:
                raise PersonalTelegramTransportError(
                    "personal_telegram_journal_permissions_invalid"
                )
            if path.exists() and path.stat().st_mode & 0o077:
                raise PersonalTelegramTransportError(
                    "personal_telegram_journal_permissions_invalid"
                )
        self._path = path
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    token_digest TEXT PRIMARY KEY,
                    actor_ref TEXT NOT NULL,
                    session_ref TEXT NOT NULL,
                    peer_ref TEXT NOT NULL,
                    text_digest TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    random_id INTEGER NOT NULL,
                    outcome TEXT NOT NULL CHECK (outcome IN ('unknown', 'sent', 'rejected')),
                    receipt_ref TEXT,
                    external_effect INTEGER CHECK (external_effect IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._path.is_symlink():
            raise PersonalTelegramTransportError("personal_telegram_journal_path_invalid")
        descriptor = os.open(
            self._path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        os.close(descriptor)
        if os.name != "nt" and self._path.stat().st_mode & 0o077:
            raise PersonalTelegramTransportError("personal_telegram_journal_permissions_invalid")
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
        peer_ref: str,
        exact_text: str,
        expires_at: datetime,
    ) -> str:
        if expires_at.tzinfo is None:
            raise PersonalTelegramTransportError("personal_telegram_expiry_invalid")
        digest = _secret_digest(approval_token)
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (
                        digest,
                        _require_prefixed_ref(actor_ref, "actor_", "actor_ref"),
                        _require_prefixed_ref(session_ref, "session_", "session_ref"),
                        _require_prefixed_ref(peer_ref, "peer_", "peer_ref"),
                        _exact_text_digest(exact_text),
                        expires_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PersonalTelegramTransportError("personal_telegram_approval_duplicate") from exc
        return digest

    def revoke_approval(self, approval_token: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE approvals SET consumed = 1 WHERE token_digest = ? AND consumed = 0",
                (_secret_digest(approval_token),),
            )
            return cursor.rowcount == 1

    def reserve(self, command: ExactSendCommand) -> _Reservation:
        if command.now.tzinfo is None:
            raise PersonalTelegramTransportError("personal_telegram_now_invalid")
        actor = _require_prefixed_ref(command.actor_ref, "actor_", "actor_ref")
        session = _require_prefixed_ref(command.session_ref, "session_", "session_ref")
        peer = _require_prefixed_ref(command.peer_ref, "peer_", "peer_ref")
        key = _require_prefixed_ref(command.idempotency_key, "send_", "idempotency_key")
        text_digest = _exact_text_digest(command.exact_text)
        fingerprint = _digest(
            {
                "actor_ref": actor,
                "session_ref": session,
                "peer_ref": peer,
                "exact_text_digest": text_digest,
                "idempotency_key": key,
            }
        )
        random_id = _deterministic_random_id(
            idempotency_key=key, peer_ref=peer, exact_text_digest=text_digest
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM attempts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise PersonalTelegramTransportError("personal_telegram_idempotency_conflict")
                return _Reservation(self._as_result(previous, replay=True), replay=True)

            approval = db.execute(
                "SELECT * FROM approvals WHERE token_digest = ?",
                (_secret_digest(command.approval_token),),
            ).fetchone()
            if approval is None:
                raise PersonalTelegramTransportError("personal_telegram_approval_unknown")
            if approval["consumed"]:
                raise PersonalTelegramTransportError("personal_telegram_approval_consumed")
            if datetime.fromisoformat(approval["expires_at"]) <= command.now:
                raise PersonalTelegramTransportError("personal_telegram_approval_expired")
            if approval["actor_ref"] != actor:
                raise PersonalTelegramTransportError("personal_telegram_actor_mismatch")
            if approval["session_ref"] != session:
                raise PersonalTelegramTransportError("personal_telegram_session_mismatch")
            if approval["peer_ref"] != peer:
                raise PersonalTelegramTransportError("personal_telegram_peer_mismatch")
            if approval["text_digest"] != text_digest:
                raise PersonalTelegramTransportError("personal_telegram_exact_text_mismatch")

            db.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, 'unknown', NULL, NULL, ?)",
                (key, fingerprint, random_id, command.now.isoformat()),
            )
            db.execute(
                "UPDATE approvals SET consumed = 1 WHERE token_digest = ?",
                (_secret_digest(command.approval_token),),
            )
            db.commit()  # The provider must only be called after this commit.
        return _Reservation(
            ExactSendResult(ProviderOutcome.UNKNOWN, None, random_id, False, None),
            replay=False,
        )

    def finalize(self, command: ExactSendCommand, receipt: ProviderSendReceipt) -> ExactSendResult:
        if receipt.provider_receipt_ref is not None:
            _require_opaque_ref(receipt.provider_receipt_ref, "provider_receipt_ref")
        if receipt.outcome is ProviderOutcome.SENT and (
            receipt.provider_receipt_ref is None or receipt.external_effect_performed is not True
        ):
            receipt = ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
        if receipt.outcome is ProviderOutcome.REJECTED and (
            receipt.external_effect_performed is not False
            or receipt.provider_receipt_ref is not None
        ):
            receipt = ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
        if receipt.outcome is ProviderOutcome.UNKNOWN:
            receipt = ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
        key = _require_prefixed_ref(command.idempotency_key, "send_", "idempotency_key")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM attempts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if previous is None:
                raise PersonalTelegramTransportError("personal_telegram_attempt_missing")
            expected = _digest(
                {
                    "actor_ref": command.actor_ref,
                    "session_ref": command.session_ref,
                    "peer_ref": command.peer_ref,
                    "exact_text_digest": _exact_text_digest(command.exact_text),
                    "idempotency_key": key,
                }
            )
            if previous["fingerprint"] != expected:
                raise PersonalTelegramTransportError("personal_telegram_idempotency_conflict")
            if previous["outcome"] != "unknown" or previous["receipt_ref"] is not None:
                return self._as_result(previous, replay=True)
            db.execute(
                "UPDATE attempts SET outcome = ?, receipt_ref = ?, external_effect = ? "
                "WHERE idempotency_key = ?",
                (
                    receipt.outcome.value,
                    receipt.provider_receipt_ref,
                    None
                    if receipt.external_effect_performed is None
                    else int(receipt.external_effect_performed),
                    key,
                ),
            )
            db.commit()
            result = db.execute(
                "SELECT * FROM attempts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            assert result is not None
            return self._as_result(result, replay=False)

    @staticmethod
    def _as_result(row: sqlite3.Row, *, replay: bool) -> ExactSendResult:
        effect = row["external_effect"]
        return ExactSendResult(
            outcome=ProviderOutcome(row["outcome"]),
            provider_receipt_ref=row["receipt_ref"],
            deterministic_random_id=row["random_id"],
            idempotent_replay=replay,
            external_effect_performed=None if effect is None else bool(effect),
        )


class SendExactPersonalTelegramLocalService:
    """A shared Gate fence and local journal must both reserve before send."""

    def __init__(
        self,
        *,
        policy: PersonalTelegramPolicy,
        journal: SqlitePersonalTelegramSendJournal,
        transport: PersonalTelegramTransport,
        shared_fence: PersonalSendFencePort,
    ) -> None:
        self._policy = policy
        self._journal = journal
        self._transport = transport
        self._shared_fence = shared_fence

    async def execute(
        self, command: ExactSendCommand, *, fence_command: PersonalSendFenceCommand
    ) -> ExactSendResult:
        if not self._policy.send_enabled:
            raise PersonalTelegramTransportError("personal_telegram_send_disabled")
        if (
            fence_command.actor_ref != command.actor_ref
            or fence_command.session_ref != command.session_ref
            or fence_command.peer_ref != command.peer_ref
            or fence_command.idempotency_key != command.idempotency_key
            or fence_command.exact_text_digest != _exact_text_digest(command.exact_text)
            or fence_command.approval_digest != _secret_digest(command.approval_token)
        ):
            raise PersonalTelegramTransportError("personal_telegram_shared_scope_mismatch")
        shared = await self._shared_fence.reserve(fence_command)
        if shared.replay:
            return _shared_replay(command, shared)
        try:
            reservation = self._journal.reserve(command)
        except Exception:
            # The provider was never called; release this newly claimed draft.
            # If finalization is unavailable, UNKNOWN remains as a safe fence.
            try:
                await self._shared_fence.finalize(
                    fence_command,
                    outcome="rejected",
                    receipt_ref=None,
                    external_effect_performed=False,
                )
            except Exception:
                pass
            raise
        if reservation.replay:
            local_replay = replace(reservation.result, idempotent_replay=True)
            try:
                await self._shared_fence.finalize(
                    fence_command,
                    outcome=local_replay.outcome.value,
                    receipt_ref=local_replay.provider_receipt_ref,
                    external_effect_performed=local_replay.external_effect_performed,
                )
            except Exception:
                return replace(
                    local_replay, outcome=ProviderOutcome.UNKNOWN, provider_receipt_ref=None
                )
            return local_replay
        try:
            receipt = await self._transport.send_exact(
                session_ref=command.session_ref,
                peer_ref=command.peer_ref,
                exact_text=command.exact_text,
                deterministic_random_id=reservation.result.deterministic_random_id,
            )
        except Exception:
            receipt = ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
        local = self._journal.finalize(command, receipt)
        try:
            shared_outcome = await self._shared_fence.finalize(
                fence_command,
                outcome=local.outcome.value,
                receipt_ref=local.provider_receipt_ref,
                external_effect_performed=local.external_effect_performed,
            )
            if (
                shared_outcome.outcome == local.outcome.value
                and shared_outcome.receipt_ref == local.provider_receipt_ref
            ):
                return local
        except Exception:
            pass
        # The local journal retains any confirmed effect. Gate's shared fence
        # stays ambiguous until the operator reconciles it; never retry.
        return ExactSendResult(
            ProviderOutcome.UNKNOWN,
            None,
            local.deterministic_random_id,
            False,
            local.external_effect_performed,
        )


def _shared_replay(command: ExactSendCommand, shared: PersonalSendFenceResult) -> ExactSendResult:
    digest = _exact_text_digest(command.exact_text)
    random_id = _deterministic_random_id(
        idempotency_key=command.idempotency_key,
        peer_ref=command.peer_ref,
        exact_text_digest=digest,
    )
    if shared.outcome == "sent" and shared.receipt_ref is not None:
        return ExactSendResult(ProviderOutcome.SENT, shared.receipt_ref, random_id, True, True)
    if shared.outcome == "rejected":
        return ExactSendResult(ProviderOutcome.REJECTED, None, random_id, True, False)
    return ExactSendResult(ProviderOutcome.UNKNOWN, None, random_id, True, None)
