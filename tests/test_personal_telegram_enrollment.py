"""Synthetic tests for protected personal Telegram enrollment."""

from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from app.personal_telegram import enrollment
from app.personal_telegram.enrollment import (
    EncryptedSessionFileStore,
    PersonalTelegramEnrollmentError,
    enroll_personal_telegram_session,
    generate_encryption_key_file,
    read_encryption_key_file,
)

SECRET_SESSION = "synthetic-session-secret-never-emit"
SECRET_HASH = "synthetic-api-hash-never-emit"
SECRET_PHONE = "+000000000000"


@dataclass(slots=True)
class FakeAuthorizer:
    session_string: str = SECRET_SESSION

    async def authorize(self, *, api_id: int, api_hash: str, phone: str) -> str:
        assert api_id == 12345
        assert api_hash == SECRET_HASH
        assert phone == SECRET_PHONE
        return self.session_string


def _store(directory: Path, key: bytes) -> EncryptedSessionFileStore:
    return EncryptedSessionFileStore(
        output_directory=directory,
        encryption_key=key,
        session_ref_factory=lambda: "session_synthetic0001",
    )


def test_enrollment_encrypts_session_and_writes_only_redacted_receipt(
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    artifacts = asyncio.run(
        enroll_personal_telegram_session(
            authorizer=FakeAuthorizer(),
            store=_store(tmp_path / "protected", key),
            api_id=12345,
            api_hash=SECRET_HASH,
            phone=SECRET_PHONE,
            account_label="Alfa owner",
            application_label="Alfa Personal",
            created_at=datetime(2026, 7, 21, 18, 0, tzinfo=UTC),
        )
    )

    ciphertext = artifacts.ciphertext_path.read_bytes()
    receipt_text = artifacts.receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)

    assert SECRET_SESSION.encode() not in ciphertext
    assert Fernet(key).decrypt(ciphertext).decode() == SECRET_SESSION
    assert SECRET_SESSION not in receipt_text
    assert SECRET_HASH not in receipt_text
    assert SECRET_PHONE not in receipt_text
    assert receipt["session_ref"] == "session_synthetic0001"
    assert receipt["read_dialogs_enabled"] is False
    assert receipt["contact_sync_enabled"] is False
    assert receipt["send_enabled"] is False
    assert stat.S_IMODE(artifacts.ciphertext_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifacts.receipt_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifacts.ciphertext_path.parent.stat().st_mode) == 0o700


def test_enrollment_never_overwrites_existing_artifacts(tmp_path: Path) -> None:
    store = _store(tmp_path, Fernet.generate_key())
    store.persist(
        session_string=SECRET_SESSION,
        account_label="Alfa owner",
        application_label="Alfa Personal",
    )

    with pytest.raises(
        PersonalTelegramEnrollmentError,
        match="personal_telegram_enrollment_artifact_exists",
    ):
        store.persist(
            session_string="different-secret",
            account_label="Alfa owner",
            application_label="Alfa Personal",
        )

    assert SECRET_SESSION not in str(tmp_path)


def test_key_file_is_owner_only_and_never_overwritten(tmp_path: Path) -> None:
    key_file = tmp_path / "keys" / "personal.key"
    generate_encryption_key_file(key_file)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700
    first = key_file.read_bytes()

    with pytest.raises(
        PersonalTelegramEnrollmentError,
        match="personal_telegram_key_file_exists",
    ):
        generate_encryption_key_file(key_file)

    assert key_file.read_bytes() == first


def test_windows_key_is_dpapi_bound_and_unwrapped_for_same_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_dpapi(value: bytes, *, protect: bool) -> bytes:
        marker = b"synthetic-current-user:"
        if protect:
            return marker + value[::-1]
        assert value.startswith(marker)
        return value[len(marker) :][::-1]

    monkeypatch.setattr(enrollment, "_uses_windows_current_user_dpapi", lambda: True)
    monkeypatch.setattr(enrollment, "_windows_dpapi_transform", fake_dpapi)
    key_file = tmp_path / "personal-telegram.fernet.key.dpapi"

    generate_encryption_key_file(key_file)
    stored = key_file.read_bytes()
    key = read_encryption_key_file(key_file)

    assert stored.startswith(enrollment._DPAPI_KEY_PREFIX)
    assert key not in stored
    Fernet(key)


def test_windows_rejects_unwrapped_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enrollment, "_uses_windows_current_user_dpapi", lambda: True)
    key_file = tmp_path / "unwrapped.fernet.key"
    key_file.write_bytes(Fernet.generate_key())

    with pytest.raises(
        PersonalTelegramEnrollmentError,
        match="personal_telegram_windows_dpapi_key_required",
    ):
        read_encryption_key_file(key_file)


def test_windows_receipt_records_dpapi_store_without_enabling_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enrollment, "_uses_windows_current_user_dpapi", lambda: True)
    store = EncryptedSessionFileStore(
        output_directory=tmp_path,
        encryption_key=Fernet.generate_key(),
        session_ref_factory=lambda: "session_synthetic0001",
    )

    artifacts = store.persist(
        session_string=SECRET_SESSION,
        account_label="Alfa owner",
        application_label="Alfa Personal",
    )

    assert artifacts.receipt.secret_store_class == (
        "windows_current_user_dpapi_wrapped_fernet"
    )
    assert artifacts.receipt.read_dialogs_enabled is False
    assert artifacts.receipt.contact_sync_enabled is False
    assert artifacts.receipt.send_enabled is False


def test_errors_are_redacted_when_authorization_fails(tmp_path: Path) -> None:
    class FailingAuthorizer:
        async def authorize(self, *, api_id: int, api_hash: str, phone: str) -> str:
            raise RuntimeError(f"provider leaked {api_hash} {phone}")

    with pytest.raises(
        PersonalTelegramEnrollmentError,
        match="personal_telegram_enrollment_failed",
    ) as captured:
        asyncio.run(
            enroll_personal_telegram_session(
                authorizer=FailingAuthorizer(),
                store=_store(tmp_path, Fernet.generate_key()),
                api_id=12345,
                api_hash=SECRET_HASH,
                phone=SECRET_PHONE,
                account_label="Alfa owner",
                application_label="Alfa Personal",
            )
        )

    assert SECRET_HASH not in str(captured.value)
    assert SECRET_PHONE not in str(captured.value)
