"""Server-side access to the owner-enrolled RU envelope on the specific S2 volume."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from stat import S_ISREG

from cryptography.fernet import Fernet

from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.personal_telegram.cloud_session import StoredCloudSession, open_cloud_session

PROJECT_ID = "4d14a47e-0f3c-47cf-9eb9-893fae7e2044"
SERVICE_ID = "0a53bcba-9be8-459d-bca9-14c55c4b1b96"
VAULT = Path("/gate-vault")
ENVELOPE = "ru-session.bin"
SESSION_REF = "session_gate_s2_ru"


def require_s2_mount(environment: Mapping[str, str]) -> None:
    if (
        environment.get("RAILWAY_PROJECT_ID") != PROJECT_ID
        or environment.get("RAILWAY_SERVICE_ID") != SERVICE_ID
        or environment.get("RAILWAY_ENVIRONMENT_NAME") != "staging"
        or environment.get("RAILWAY_VOLUME_MOUNT_PATH") != str(VAULT)
    ):
        raise PersonalTelegramTransportError("s2_vault_scope_mismatch")
    try:
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
        if any(len(parts) > 4 and parts[4] == str(VAULT) for parts in map(str.split, mounts)):
            return
    except OSError:
        pass
    raise PersonalTelegramTransportError("s2_vault_not_mounted")


def read_s2_session(vault: Path, cipher: Fernet, *, api_id: int) -> StoredCloudSession:
    try:
        fd = os.open(vault / ENVELOPE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError
            encrypted = handle.read(16385)
    except OSError:
        raise PersonalTelegramTransportError("s2_vault_unavailable") from None
    value = open_cloud_session(cipher, encrypted)
    if (value.api_id, value.session_ref) != (api_id, SESSION_REF):
        raise PersonalTelegramTransportError("s2_vault_identity_mismatch")
    return value


def open_runtime_s2_session(environment: Mapping[str, str]) -> StoredCloudSession:
    require_s2_mount(environment)
    try:
        api_id = int(environment["GATE_PERSONAL_TELEGRAM_API_ID"])
        cipher = Fernet(environment["GATE_S2_SESSION_KEY"].encode("ascii"))
    except (KeyError, ValueError, UnicodeError):
        raise PersonalTelegramTransportError("s2_vault_binding_invalid") from None
    return read_s2_session(VAULT, cipher, api_id=api_id)
