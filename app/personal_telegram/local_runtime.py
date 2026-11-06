"""Opt-in MTProto sender on the Windows profile that owns the DPAPI key.

No Telegram account, peer, credential or session is selected from model output.
There is no server/MCP binding and importing this module has no network effect.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.application.personal_telegram_transport import (
    PersonalTelegramTransportError,
    ProviderOutcome,
    ProviderSendReceipt,
    _exact_text_digest,
    _require_prefixed_ref,
)
from app.personal_telegram.enrollment import (
    PersonalTelegramEnrollmentError,
    read_encryption_key_file,
)


class LocalEncryptedSessionSource:
    """Unseal an exact local enrollment without exporting it from this process."""

    def __init__(
        self,
        *,
        key_file: Path,
        receipt_file: Path,
        require_windows: bool = True,
    ) -> None:
        self._key_file = key_file
        self._receipt_file = receipt_file
        self._require_windows = require_windows

    def load(self, *, session_ref: str) -> str:
        _require_prefixed_ref(session_ref, "session_", "session_ref")
        if self._require_windows and os.name != "nt":
            raise PersonalTelegramTransportError("personal_telegram_original_windows_host_required")
        if (
            self._key_file.is_symlink()
            or self._receipt_file.is_symlink()
            or not self._key_file.is_file()
            or not self._receipt_file.is_file()
        ):
            raise PersonalTelegramTransportError("personal_telegram_local_session_not_bound")
        try:
            receipt = json.loads(self._receipt_file.read_text(encoding="utf-8"))
            expected_class = (
                "windows_current_user_dpapi_wrapped_fernet"
                if os.name == "nt"
                else "posix_owner_only_fernet_file"
            )
            if (
                not isinstance(receipt, dict)
                or receipt.get("session_ref") != session_ref
                or self._receipt_file.name != f"{session_ref}.receipt.json"
                or receipt.get("secret_store_class") != expected_class
                or receipt.get("send_enabled") is not False
                or receipt.get("read_dialogs_enabled") is not False
                or receipt.get("contact_sync_enabled") is not False
            ):
                raise ValueError("receipt_mismatch")
            ciphertext = self._receipt_file.with_name(f"{session_ref}.session.enc")
            if ciphertext.is_symlink() or not ciphertext.is_file():
                raise ValueError("ciphertext_missing")
            if os.name != "nt" and any(
                path.stat().st_mode & 0o077
                for path in (self._key_file, self._receipt_file, ciphertext)
            ):
                raise ValueError("file_permissions_too_broad")
            key = read_encryption_key_file(self._key_file)
            session = Fernet(key).decrypt(ciphertext.read_bytes()).decode("utf-8")
            if not session or session != session.strip():
                raise ValueError("session_invalid")
            return session
        except (
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            InvalidToken,
            PersonalTelegramEnrollmentError,
        ):
            raise PersonalTelegramTransportError(
                "personal_telegram_local_session_unavailable"
            ) from None


@dataclass(frozen=True, slots=True)
class PinnedUserPeer:
    """Owner-provisioned account-specific peer pin, never a guessed username."""

    peer_ref: str
    user_id: int
    access_hash: int


class PinnedPersonalPeerResolver(Protocol):
    def resolve(self, *, session_ref: str, peer_ref: str) -> PinnedUserPeer: ...


class TelethonPersonalTelegramTransport:
    """One raw MTProto send, with Telethon retries and flood sleeps disabled."""

    def __init__(
        self,
        *,
        source: LocalEncryptedSessionSource,
        peers: PinnedPersonalPeerResolver,
        api_id: int,
        api_hash: str,
        expected_owner_user_id: int,
    ) -> None:
        if (
            isinstance(api_id, bool)
            or api_id <= 0
            or not api_hash
            or isinstance(expected_owner_user_id, bool)
            or expected_owner_user_id <= 0
        ):
            raise PersonalTelegramTransportError("personal_telegram_runtime_binding_invalid")
        self._source = source
        self._peers = peers
        self._api_id = api_id
        self._api_hash = api_hash
        self._expected_owner_user_id = expected_owner_user_id

    async def send_exact(
        self,
        *,
        session_ref: str,
        peer_ref: str,
        exact_text: str,
        deterministic_random_id: int,
    ) -> ProviderSendReceipt:
        _require_prefixed_ref(session_ref, "session_", "session_ref")
        _require_prefixed_ref(peer_ref, "peer_", "peer_ref")
        _exact_text_digest(exact_text)
        if not 1 <= deterministic_random_id < 2**63:
            raise PersonalTelegramTransportError("personal_telegram_random_id_invalid")
        pin = self._peers.resolve(session_ref=session_ref, peer_ref=peer_ref)
        if (
            pin.peer_ref != peer_ref
            or isinstance(pin.user_id, bool)
            or pin.user_id <= 0
            or isinstance(pin.access_hash, bool)
            or pin.access_hash == 0
        ):
            raise PersonalTelegramTransportError("personal_telegram_peer_pin_invalid")
        session = self._source.load(session_ref=session_ref)
        try:
            from telethon import TelegramClient, functions, types  # type: ignore[import-untyped]
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
                return ProviderSendReceipt(ProviderOutcome.REJECTED, None, False)
            owner = await client.get_me()
            if owner is None or owner.id != self._expected_owner_user_id:
                return ProviderSendReceipt(ProviderOutcome.REJECTED, None, False)
            request = functions.messages.SendMessageRequest(
                peer=types.InputPeerUser(pin.user_id, pin.access_hash),
                message=exact_text,
                random_id=deterministic_random_id,
                no_webpage=True,
            )
            response = await asyncio.wait_for(client(request), timeout=15)
            message_id = _confirmed_message_id(response, deterministic_random_id)
            if message_id is None:
                return ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
            return ProviderSendReceipt(ProviderOutcome.SENT, f"telegram_{message_id}", True)
        except Exception:
            # After connect or request the result may be ambiguous, including
            # FloodWait and timeouts. Never call Telegram again to retry here.
            return ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


def _confirmed_message_id(response: Any, random_id: int) -> int | None:
    """Accept only MTProto's explicit random-id mapping or short sent update."""
    if type(response).__name__ == "UpdateShortSentMessage":
        identifier = getattr(response, "id", None)
        return identifier if type(identifier) is int and identifier > 0 else None
    for update in getattr(response, "updates", ()):
        if (
            type(update).__name__ == "UpdateMessageID"
            and getattr(update, "random_id", None) == random_id
        ):
            identifier = getattr(update, "id", None)
            return identifier if type(identifier) is int and identifier > 0 else None
    return None
