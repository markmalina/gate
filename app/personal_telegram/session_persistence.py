"""Version-pinned encrypted StringSession storage; no network on import/load."""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.personal_telegram.command_scope import CommandRuntimeError, RuCommandScope

SESSION_SCHEMA = "gate.personal-command-session/v1"
SUPPORTED_TELETHON = frozenset({"1.44.0"})


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedSessionRecord:
    ciphertext: bytes
    revision: int
    key_version: str
    state: str


class SessionRepository(Protocol):
    async def load(self, scope: RuCommandScope) -> EncryptedSessionRecord: ...

    async def compare_and_swap(
        self,
        scope: RuCommandScope,
        *,
        expected_revision: int,
        ciphertext: bytes,
        key_version: str,
        state: str,
    ) -> int: ...


class SessionCipher:
    """Authenticated envelope binds ciphertext to RU, workspace, owner and generation."""

    def __init__(self, *, key: bytes, key_version: str) -> None:
        if not key_version or len(key_version) > 64:
            raise CommandRuntimeError("session_key_version_invalid")
        self._fernet = Fernet(key)
        self.key_version = key_version

    def seal(self, scope: RuCommandScope, *, session: str, generation: int) -> bytes:
        scope.validate()
        if type(generation) is not int or generation <= 0 or len(session) > 4096:
            raise CommandRuntimeError("session_envelope_invalid")
        return self._fernet.encrypt(
            json.dumps(
                {
                    "schema": SESSION_SCHEMA,
                    "workspace": scope.workspace,
                    "account": str(scope.account_id),
                    "binding": str(scope.binding_id),
                    "owner": scope.owner_subject,
                    "generation": generation,
                    "session": session,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )

    def unseal(self, scope: RuCommandScope, *, ciphertext: bytes, generation: int) -> str:
        scope.validate()
        try:
            data = json.loads(self._fernet.decrypt(ciphertext))
            expected = {
                "schema": SESSION_SCHEMA,
                "workspace": scope.workspace,
                "account": str(scope.account_id),
                "binding": str(scope.binding_id),
                "owner": scope.owner_subject,
                "generation": generation,
            }
            if any(data.get(k) != v for k, v in expected.items()):
                raise ValueError
            session = data.get("session")
            if not isinstance(session, str) or len(session) > 4096:
                raise ValueError
            return session
        except (InvalidToken, UnicodeError, ValueError, TypeError, AttributeError):
            raise CommandRuntimeError("session_envelope_unavailable") from None


async def open_encrypted_session(
    *,
    scope: RuCommandScope,
    generation: int,
    cipher: SessionCipher,
    repository: SessionRepository,
) -> Any:
    """Return a Telethon StringSession with serialized async persistence hooks.

    Telethon's async hooks are experimental. Keep this adapter isolated, pinned,
    and exercised with the actual client callbacks. No full entity/update mirror.
    """
    scope.validate()
    import telethon  # type: ignore[import-untyped]
    from telethon.sessions import MemorySession, StringSession  # type: ignore[import-untyped]

    if telethon.__version__ not in SUPPORTED_TELETHON:
        raise CommandRuntimeError("telethon_session_contract_unverified")
    record = await repository.load(scope)
    if record.key_version != cipher.key_version or record.state == "revoked":
        raise CommandRuntimeError("session_record_unavailable")
    value = cipher.unseal(scope, ciphertext=record.ciphertext, generation=generation)

    class PersistedStringSession(StringSession):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(value or None)
            self.revision = record.revision
            self.persistence_failed = False
            self._save_lock = asyncio.Lock()

        async def save(self) -> None:
            async with self._save_lock:
                if self.persistence_failed:
                    raise CommandRuntimeError("session_persistence_failed")
                try:
                    serialized = StringSession.save(self)
                    state = "prepared" if serialized else "transition"
                    self.revision = await repository.compare_and_swap(
                        scope,
                        expected_revision=self.revision,
                        ciphertext=cipher.seal(
                            scope,
                            session=serialized,
                            generation=generation,
                        ),
                        key_version=cipher.key_version,
                        state=state,
                    )
                except BaseException as exc:
                    # A cancelled CAS may already have committed. Never retry with
                    # stale revision or silently use the earlier credential.
                    self.persistence_failed = True
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise CommandRuntimeError("session_persistence_failed") from None

        async def close(self) -> None:
            await self.save()

        async def delete(self) -> None:
            # Telethon logout must not delete authorisation without an explicit
            # revoke action. Runtime disconnect calls close, not delete.
            raise CommandRuntimeError("explicit_session_revoke_required")

        def clone(self, to_instance: Any = None) -> Any:
            if to_instance is not None:
                raise CommandRuntimeError("session_clone_target_denied")
            return MemorySession()

    try:
        return PersistedStringSession()
    except (ValueError, TypeError, struct.error):
        raise CommandRuntimeError("session_format_invalid") from None
