"""Capped, read-only Telegram authorization check on a protected session source."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.application.personal_telegram_transport import (
    PersonalTelegramTransportError,
    _require_prefixed_ref,
)
from app.personal_telegram.local_runtime import PersonalTelegramSessionSource


@dataclass(frozen=True, slots=True)
class LocalAuthorizationStatus:
    configured: bool
    authenticated: bool | None
    runtime_connected: bool
    probe_connected: bool
    send_enabled: bool
    create_group_enabled: bool
    last_successful_check: str | None
    error_code: str | None

    def as_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _status(
    *,
    configured: bool = False,
    authenticated: bool | None = None,
    connected: bool = False,
    checked: bool = False,
    error_code: str | None = None,
) -> LocalAuthorizationStatus:
    return LocalAuthorizationStatus(
        configured=configured,
        authenticated=authenticated,
        runtime_connected=False,
        probe_connected=connected,
        send_enabled=False,
        create_group_enabled=False,
        last_successful_check=datetime.now(UTC).isoformat() if checked else None,
        error_code=error_code,
    )


async def check_local_authorization(
    *,
    source: PersonalTelegramSessionSource,
    session_ref: str,
    api_id: int,
    api_hash: str,
    expected_owner_user_id: int,
) -> LocalAuthorizationStatus:
    """Connect once, check authorization and exact owner, then disconnect.

    No dialogs, contacts, messages, provider sends or group operations.
    The session is unsealed in this process and never returned.
    """
    if (
        type(api_id) is not int
        or api_id <= 0
        or not isinstance(api_hash, str)
        or not api_hash
        or type(expected_owner_user_id) is not int
        or expected_owner_user_id <= 0
    ):
        return _status(error_code="binding_invalid")
    try:
        _require_prefixed_ref(session_ref, "session_", "session_ref")
        session = source.load(session_ref=session_ref)
    except (PersonalTelegramTransportError, OSError):
        return _status(error_code="local_session_unavailable")
    try:
        from telethon import TelegramClient  # type: ignore[import-untyped]
        from telethon.sessions import StringSession  # type: ignore[import-untyped]
    except ImportError:
        return _status(configured=True, error_code="telethon_not_installed")

    try:
        client: Any = TelegramClient(
            StringSession(session),
            api_id,
            api_hash,
            receive_updates=False,
            request_retries=0,
            connection_retries=0,
            auto_reconnect=False,
            flood_sleep_threshold=0,
            raise_last_call_error=True,
        )
    except Exception:
        return _status(configured=True, error_code="client_unavailable")
    connected = False
    try:
        await asyncio.wait_for(client.connect(), timeout=10)
        connected = True
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10)
        if not authorized:
            return _status(
                configured=True,
                authenticated=False,
                connected=True,
                error_code="session_unauthorized",
            )
        owner = await asyncio.wait_for(client.get_me(), timeout=10)
        if owner is None or getattr(owner, "id", None) != expected_owner_user_id:
            return _status(
                configured=True, authenticated=False, connected=True, error_code="owner_mismatch"
            )
        return _status(configured=True, authenticated=True, connected=True, checked=True)
    except Exception:
        return _status(
            configured=True, connected=connected, error_code="provider_check_unavailable"
        )
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except Exception:
            pass

