"""Local encrypted enrollment for one owner-controlled Telegram session."""

from __future__ import annotations

import ctypes
import json
import os
import secrets
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet

_DPAPI_KEY_PREFIX = b"GATE-DPAPI-CURRENT-USER-V1\x00"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class PersonalTelegramEnrollmentError(RuntimeError):
    """A redacted, fail-closed enrollment error."""


class _DataBlob(ctypes.Structure):
    """Windows DATA_BLOB used by the native DPAPI boundary."""

    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class PersonalTelegramSessionAuthorizer(Protocol):
    """Authorize one account and return a secret session string in memory only."""

    async def authorize(self, *, api_id: int, api_hash: str, phone: str) -> str:
        """Return one authorized secret session string."""
        ...


@dataclass(frozen=True, slots=True)
class EnrollmentReceipt:
    """Non-secret capability receipt; all runtime capabilities remain disabled."""

    session_ref: str
    account_label: str
    application_label: str
    created_at: str
    secret_store_class: str = "local_fernet_file"
    read_dialogs_enabled: bool = False
    contact_sync_enabled: bool = False
    send_enabled: bool = False

    def as_json(self) -> str:
        """Return deterministic redacted JSON."""

        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class EnrollmentArtifacts:
    """Local paths plus the safe receipt returned to the operator."""

    ciphertext_path: Path
    receipt_path: Path
    receipt: EnrollmentReceipt


class EncryptedSessionFileStore:
    """Persist session ciphertext and a separate redacted receipt."""

    def __init__(
        self,
        *,
        output_directory: Path,
        encryption_key: bytes,
        session_ref_factory: Callable[[], str] | None = None,
        secret_store_class: str | None = None,
    ) -> None:
        self._output_directory = output_directory
        try:
            self._fernet = Fernet(encryption_key)
        except (TypeError, ValueError) as exc:
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_encryption_key_invalid"
            ) from exc
        self._session_ref_factory = session_ref_factory or _new_session_ref
        self._secret_store_class = secret_store_class or _secret_store_class()

    def persist(
        self,
        *,
        session_string: str,
        account_label: str,
        application_label: str,
        created_at: datetime | None = None,
    ) -> EnrollmentArtifacts:
        """Encrypt before persistence and never overwrite existing artifacts."""

        if not session_string or session_string != session_string.strip():
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_session_material_invalid"
            )
        account_label = _safe_label(account_label, "account_label")
        application_label = _safe_label(application_label, "application_label")
        session_ref = _safe_session_ref(self._session_ref_factory())
        timestamp = created_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_created_at_must_be_timezone_aware"
            )

        self._output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not _uses_windows_current_user_dpapi():
            os.chmod(self._output_directory, 0o700)
        ciphertext_path = self._output_directory / f"{session_ref}.session.enc"
        receipt_path = self._output_directory / f"{session_ref}.receipt.json"
        if ciphertext_path.exists() or receipt_path.exists():
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_enrollment_artifact_exists"
            )

        ciphertext = self._fernet.encrypt(session_string.encode("utf-8"))
        receipt = EnrollmentReceipt(
            session_ref=session_ref,
            account_label=account_label,
            application_label=application_label,
            created_at=timestamp.astimezone(UTC).isoformat(),
            secret_store_class=self._secret_store_class,
        )
        try:
            _write_new_private_file(ciphertext_path, ciphertext)
            _write_new_private_file(receipt_path, receipt.as_json().encode("utf-8"))
        except Exception:
            _remove_created(receipt_path)
            _remove_created(ciphertext_path)
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_enrollment_persistence_failed"
            ) from None

        return EnrollmentArtifacts(
            ciphertext_path=ciphertext_path,
            receipt_path=receipt_path,
            receipt=receipt,
        )


class TelethonInteractiveSessionAuthorizer:
    """Late-bound Telethon authorization with non-echoing code and 2FA prompts."""

    def __init__(
        self,
        *,
        code_prompt: Callable[[], str],
        password_prompt: Callable[[], str],
    ) -> None:
        self._code_prompt = code_prompt
        self._password_prompt = password_prompt

    async def authorize(self, *, api_id: int, api_hash: str, phone: str) -> str:
        if api_id <= 0 or not api_hash or not phone:
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_credentials_incomplete"
            )
        try:
            from telethon import TelegramClient  # type: ignore[import-untyped]
            from telethon.sessions import StringSession  # type: ignore[import-untyped]
        except ImportError as exc:
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_telethon_not_installed"
            ) from exc

        client: Any = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            receive_updates=False,
        )
        try:
            await client.start(
                phone=lambda: phone,
                code_callback=self._code_prompt,
                password=self._password_prompt,
            )
            if not await client.is_user_authorized():
                raise PersonalTelegramEnrollmentError(
                    "personal_telegram_authorization_incomplete"
                )
            session_string = StringSession.save(client.session)
            if not session_string:
                raise PersonalTelegramEnrollmentError(
                    "personal_telegram_session_material_invalid"
                )
            return str(session_string)
        except PersonalTelegramEnrollmentError:
            raise
        except Exception as exc:
            raise PersonalTelegramEnrollmentError(
                "personal_telegram_authorization_failed"
            ) from exc
        finally:
            await client.disconnect()


async def enroll_personal_telegram_session(
    *,
    authorizer: PersonalTelegramSessionAuthorizer,
    store: EncryptedSessionFileStore,
    api_id: int,
    api_hash: str,
    phone: str,
    account_label: str,
    application_label: str,
    created_at: datetime | None = None,
) -> EnrollmentArtifacts:
    """Authorize then immediately encrypt one session; no capability is activated."""

    try:
        session_string = await authorizer.authorize(
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
        )
        return store.persist(
            session_string=session_string,
            account_label=account_label,
            application_label=application_label,
            created_at=created_at,
        )
    except PersonalTelegramEnrollmentError:
        raise
    except Exception as exc:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_enrollment_failed"
        ) from exc


def generate_encryption_key_file(path: Path) -> None:
    """Create a protected Fernet key file and fail if it already exists."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _uses_windows_current_user_dpapi():
        os.chmod(path.parent, 0o700)
    stored_key = _protect_encryption_key_for_storage(Fernet.generate_key())
    try:
        _write_new_private_file(path, stored_key)
    except FileExistsError as exc:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_key_file_exists"
        ) from exc
    except OSError as exc:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_key_file_write_failed"
        ) from exc


def read_encryption_key_file(path: Path) -> bytes:
    """Read and unwrap an enrollment key without leaking it in an error."""

    try:
        value = path.read_bytes()
    except OSError as exc:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_key_file_read_failed"
        ) from exc
    if not value:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_encryption_key_invalid"
        )
    return _unprotect_encryption_key_from_storage(value)


def _uses_windows_current_user_dpapi() -> bool:
    return os.name == "nt"


def _secret_store_class() -> str:
    if _uses_windows_current_user_dpapi():
        return "windows_current_user_dpapi_wrapped_fernet"
    return "posix_owner_only_fernet_file"


def _protect_encryption_key_for_storage(key: bytes) -> bytes:
    if not _uses_windows_current_user_dpapi():
        return key
    return _DPAPI_KEY_PREFIX + _windows_dpapi_transform(key, protect=True)


def _unprotect_encryption_key_from_storage(value: bytes) -> bytes:
    if not _uses_windows_current_user_dpapi():
        return value
    if not value.startswith(_DPAPI_KEY_PREFIX):
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_windows_dpapi_key_required"
        )
    protected = value[len(_DPAPI_KEY_PREFIX) :]
    if not protected:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_windows_dpapi_key_invalid"
        )
    key = _windows_dpapi_transform(protected, protect=False)
    try:
        Fernet(key)
    except (TypeError, ValueError) as exc:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_windows_dpapi_key_invalid"
        ) from exc
    return key


def _windows_dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    """Protect or unprotect bytes with native CurrentUser Windows DPAPI."""

    win_dll: Any = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_windows_dpapi_unavailable"
        )
    input_buffer = ctypes.create_string_buffer(value)
    input_blob = _DataBlob(
        len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output_blob = _DataBlob()
    crypt32: Any = win_dll("crypt32", use_last_error=True)
    kernel32: Any = win_dll("kernel32", use_last_error=True)
    operation: Any
    if protect:
        operation = crypt32.CryptProtectData
    else:
        operation = crypt32.CryptUnprotectData
    succeeded = operation(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not succeeded:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_windows_dpapi_operation_failed"
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _new_session_ref() -> str:
    return "session_" + secrets.token_hex(16)


def _safe_session_ref(value: str) -> str:
    if (
        not value.startswith("session_")
        or not 12 <= len(value) <= 64
        or not value.replace("_", "").isalnum()
    ):
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_session_ref_invalid"
        )
    return value


def _safe_label(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 80 or any(
        character in "\r\n\0" for character in normalized
    ):
        raise PersonalTelegramEnrollmentError(
            f"personal_telegram_{field_name}_invalid"
        )
    return normalized


def _write_new_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        _remove_created(path)
        raise
    if not _uses_windows_current_user_dpapi():
        os.chmod(path, 0o600)


def _remove_created(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
