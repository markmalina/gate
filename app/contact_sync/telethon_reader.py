"""Telethon adapter restricted to contact-directory reads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.contact_sync.models import (
    ContactReadResult,
    SavedPhoneContact,
    TelegramContactUser,
)


class TelegramContactReadError(Exception):
    """Safe Telegram read error that excludes session and contact material."""


@dataclass(frozen=True, slots=True)
class TelethonRuntime:
    """Late-bound Telethon symbols keep the main Gate runtime dependency-free."""

    client_type: Any
    string_session_type: Any
    get_contacts_request_type: Any
    get_saved_request_type: Any
    takeout_delay_error_type: type[BaseException]


class TelethonContactReader:
    """Read saved labels and Telegram user identities without message access."""

    def __init__(self, *, client: Any, runtime: TelethonRuntime) -> None:
        self._client = client
        self._runtime = runtime

    async def read_contacts(self) -> ContactReadResult:
        await self._client.connect()
        try:
            if not await self._client.is_user_authorized():
                raise TelegramContactReadError("Telegram session is not authorized")
            current_user = await self._client.get_me()
            owner_user_id = _integer(getattr(current_user, "id", 0))
            if owner_user_id <= 0:
                raise TelegramContactReadError("Telegram session account identity is unavailable")
            contacts_result = await self._client(
                self._runtime.get_contacts_request_type(hash=0)
            )
            telegram_users = _telegram_users(getattr(contacts_result, "users", ()))
            try:
                async with self._client.takeout(
                    contacts=True,
                    users=False,
                    chats=False,
                    megagroups=False,
                    channels=False,
                    files=False,
                ) as takeout:
                    saved_result = await takeout(self._runtime.get_saved_request_type())
            except self._runtime.takeout_delay_error_type as exc:
                seconds = _bounded_delay_seconds(exc)
                raise TelegramContactReadError(
                    f"Telegram contact export is delayed for {seconds} seconds"
                ) from exc
            return ContactReadResult(
                saved_contacts=_saved_contacts(saved_result),
                telegram_users=telegram_users,
                source_account_telegram_user_id=str(owner_user_id),
            )
        except TelegramContactReadError:
            raise
        except Exception as exc:
            raise TelegramContactReadError("Telegram contact directory read failed") from exc
        finally:
            await self._client.disconnect()


def create_telethon_reader(
    *, api_id: int, api_hash: str, session_string: str
) -> TelethonContactReader:
    """Build a no-updates Telethon client from secret-backed session material."""

    runtime = _load_telethon()
    if api_id <= 0 or not api_hash or not session_string:
        raise TelegramContactReadError("Telegram contact-sync credentials are incomplete")
    client = runtime.client_type(
        runtime.string_session_type(session_string),
        api_id,
        api_hash,
        receive_updates=False,
    )
    return TelethonContactReader(client=client, runtime=runtime)


async def authorize_session_to_file(
    *, api_id: int, api_hash: str, phone: str, session_file: Path
) -> None:
    """Perform one-time interactive authorization and write a mode-0600 session file."""

    if session_file.suffix != ".session":
        raise TelegramContactReadError("Session output file must use the .session suffix")
    if session_file.exists():
        raise TelegramContactReadError("Session output file already exists")
    runtime = _load_telethon()
    client = runtime.client_type(
        runtime.string_session_type(),
        api_id,
        api_hash,
        receive_updates=False,
    )
    try:
        await client.start(phone=phone)
        session_string = runtime.string_session_type.save(client.session)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.touch(mode=0o600, exist_ok=False)
        session_file.write_text(session_string, encoding="utf-8")
        session_file.chmod(0o600)
    except TelegramContactReadError:
        raise
    except Exception as exc:
        raise TelegramContactReadError("Telegram session authorization failed") from exc
    finally:
        await client.disconnect()


def read_session_file(session_file: Path) -> str:
    """Load a local secret without including its content in an error."""

    try:
        value = session_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TelegramContactReadError("Telegram session file could not be read") from exc
    if not value:
        raise TelegramContactReadError("Telegram session file is empty")
    return value


def _load_telethon() -> TelethonRuntime:
    try:
        from telethon import TelegramClient, errors, functions  # type: ignore[import-untyped]
        from telethon.sessions import StringSession  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TelegramContactReadError(
            "Telethon is not installed; install the contact-sync dependency group"
        ) from exc
    return TelethonRuntime(
        client_type=TelegramClient,
        string_session_type=StringSession,
        get_contacts_request_type=functions.contacts.GetContactsRequest,
        get_saved_request_type=functions.contacts.GetSavedRequest,
        takeout_delay_error_type=errors.TakeoutInitDelayError,
    )


def _saved_contacts(values: Any) -> tuple[SavedPhoneContact, ...]:
    return tuple(
        SavedPhoneContact(
            phone=_text(getattr(value, "phone", "")),
            first_name=_text(getattr(value, "first_name", "")),
            last_name=_text(getattr(value, "last_name", "")),
            saved_at=_integer(getattr(value, "date", 0)),
        )
        for value in values
    )


def _telegram_users(values: Any) -> tuple[TelegramContactUser, ...]:
    result: list[TelegramContactUser] = []
    for value in values:
        user_id = _integer(getattr(value, "id", 0))
        phone = _text(getattr(value, "phone", ""))
        if user_id <= 0 or not phone:
            continue
        username_value = getattr(value, "username", None)
        result.append(
            TelegramContactUser(
                telegram_user_id=str(user_id),
                phone=phone,
                username=_text(username_value) if username_value else None,
            )
        )
    return tuple(result)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _bounded_delay_seconds(exc: BaseException) -> int:
    seconds = _integer(getattr(exc, "seconds", 0))
    return max(0, min(seconds, 604800))
