"""Offline integrity check for an existing owner-enrolled Telegram session.

This module cannot contact Telegram and never returns decrypted session material.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.personal_telegram.enrollment import (
    PersonalTelegramEnrollmentError,
    read_encryption_key_file,
)


@dataclass(frozen=True, slots=True)
class PersonalTelegramPreflight:
    configured: bool
    authenticated: None
    runtime_connected: bool
    send_enabled: bool
    create_group_enabled: bool
    last_successful_check: None
    local_integrity_checked_at: str | None
    error_code: str | None

    def as_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _result(
    *, configured: bool = False, checked: bool = False, error_code: str | None = None
) -> PersonalTelegramPreflight:
    return PersonalTelegramPreflight(
        configured=configured,
        authenticated=None,
        runtime_connected=False,
        send_enabled=False,
        create_group_enabled=False,
        last_successful_check=None,
        local_integrity_checked_at=datetime.now(UTC).isoformat() if checked else None,
        error_code=error_code,
    )


def check_local_enrollment(
    *, key_file: Path, receipt_file: Path
) -> PersonalTelegramPreflight:
    """Check local ciphertext integrity; leave Telegram authentication unknown.

    The existing Windows CurrentUser DPAPI key can be unwrapped only in its
    original user profile. This function never copies the key or session.
    """
    if not key_file.is_file():
        return _result(error_code="key_missing")
    if not receipt_file.is_file():
        return _result(error_code="receipt_missing")
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _result(error_code="receipt_invalid")
    if not isinstance(receipt, dict):
        return _result(error_code="receipt_invalid")
    session_ref = receipt.get("session_ref")
    expected_store = (
        "windows_current_user_dpapi_wrapped_fernet"
        if os.name == "nt"
        else "posix_owner_only_fernet_file"
    )
    if (
        not isinstance(session_ref, str)
        or not session_ref.startswith("session_")
        or not 12 <= len(session_ref) <= 64
        or not session_ref.replace("_", "").isalnum()
        or receipt_file.name != f"{session_ref}.receipt.json"
        or receipt.get("secret_store_class") != expected_store
        or receipt.get("read_dialogs_enabled") is not False
        or receipt.get("contact_sync_enabled") is not False
        or receipt.get("send_enabled") is not False
    ):
        return _result(error_code="receipt_invalid")
    ciphertext_file = receipt_file.with_name(f"{session_ref}.session.enc")
    if not ciphertext_file.is_file():
        return _result(error_code="ciphertext_missing")
    try:
        key = read_encryption_key_file(key_file)
        # Plaintext exists only inside this function and is never returned or logged.
        plaintext = Fernet(key).decrypt(ciphertext_file.read_bytes())
        valid = len(plaintext) > 0
        del plaintext
    except (
        OSError,
        ValueError,
        TypeError,
        InvalidToken,
        PersonalTelegramEnrollmentError,
    ):
        return _result(error_code="local_decryption_failed")
    if not valid:
        return _result(error_code="local_decryption_failed")
    return _result(configured=True, checked=True)
