"""Offline-only checks: no Telegram network call or secret in output."""

import json
import os

from app.personal_telegram.enrollment import (
    EncryptedSessionFileStore,
    generate_encryption_key_file,
    read_encryption_key_file,
)
from app.personal_telegram.preflight import check_local_enrollment
from cryptography.fernet import Fernet


def test_local_integrity_does_not_claim_authorization(tmp_path):
    key_file = tmp_path / "key"
    generate_encryption_key_file(key_file)
    key = read_encryption_key_file(key_file)
    artifacts = EncryptedSessionFileStore(
        output_directory=tmp_path / "sessions",
        encryption_key=key,
        session_ref_factory=lambda: "session_synthetic1234",
    ).persist(
        session_string="synthetic-session-secret",
        account_label="owner",
        application_label="test",
    )
    status = check_local_enrollment(
        key_file=key_file, receipt_file=artifacts.receipt_path
    )
    result = json.loads(status.as_json())
    assert result["configured"] is True
    assert result["authenticated"] is None
    assert result["runtime_connected"] is False
    assert result["send_enabled"] is False
    assert result["create_group_enabled"] is False
    assert result["last_successful_check"] is None
    assert result["local_integrity_checked_at"]
    assert "synthetic-session-secret" not in status.as_json()
    assert "session_synthetic1234" not in status.as_json()


def test_missing_and_corrupted_artifacts_fail_closed(tmp_path):
    key_file = tmp_path / "key"
    generate_encryption_key_file(key_file)
    receipt_file = tmp_path / "session_synthetic1234.receipt.json"
    assert (
        check_local_enrollment(key_file=key_file, receipt_file=receipt_file).error_code
        == "receipt_missing"
    )
    receipt_file.write_text(
        json.dumps(
            {
                "session_ref": "session_synthetic1234",
                "secret_store_class": (
                    "windows_current_user_dpapi_wrapped_fernet"
                    if os.name == "nt"
                    else "posix_owner_only_fernet_file"
                ),
                "read_dialogs_enabled": False,
                "contact_sync_enabled": False,
                "send_enabled": False,
            }
        )
    )
    assert (
        check_local_enrollment(key_file=key_file, receipt_file=receipt_file).error_code
        == "ciphertext_missing"
    )
    receipt_file.with_name("session_synthetic1234.session.enc").write_bytes(b"broken")
    status = check_local_enrollment(key_file=key_file, receipt_file=receipt_file)
    assert status.configured is False
    assert status.error_code == "local_decryption_failed"


def test_rejects_receipt_claiming_send_enabled(tmp_path):
    receipt_file = tmp_path / "session_synthetic1234.receipt.json"
    key_file = tmp_path / "key"
    key_file.write_bytes(Fernet.generate_key())
    receipt_file.write_text(
        json.dumps(
            {
                "session_ref": "session_synthetic1234",
                "secret_store_class": (
                    "windows_current_user_dpapi_wrapped_fernet"
                    if os.name == "nt"
                    else "posix_owner_only_fernet_file"
                ),
                "read_dialogs_enabled": False,
                "contact_sync_enabled": False,
                "send_enabled": True,
            }
        )
    )
    assert (
        check_local_enrollment(key_file=key_file, receipt_file=receipt_file).error_code
        == "receipt_invalid"
    )
