"""Encrypted cloud session binding; inspection never contacts Telegram."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from app.application.personal_telegram_transport import (
    PersonalTelegramTransportError,
    _require_prefixed_ref,
)

PREFIX = "GATE_PERSONAL_TELEGRAM_"
SCHEMA = "gate.personal-telegram.cloud-session/v1"
FIELDS = (
    "API_ID",
    "API_HASH",
    "OWNER_USER_ID",
    "SESSION_REF",
    "SESSION_CIPHERTEXT",
    "ENCRYPTION_KEY",
)


@dataclass(frozen=True, slots=True, repr=False)
class CloudSessionBinding:
    """Keep all provider identifiers and secret material out of repr/output."""

    api_id: int
    api_hash: str
    expected_owner_user_id: int
    session_ref: str
    ciphertext: str
    encryption_key: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> CloudSessionBinding:
        values = {name: environment.get(PREFIX + name, "") for name in FIELDS}
        if any(not value for value in values.values()):
            raise PersonalTelegramTransportError("cloud_binding_incomplete")
        try:
            api_id = int(values["API_ID"])
            owner_id = int(values["OWNER_USER_ID"])
            _require_prefixed_ref(values["SESSION_REF"], "session_", "session_ref")
            if (
                not 0 < api_id < 2**31
                or not 0 < owner_id < 2**63
                or not re.fullmatch(r"[0-9a-fA-F]{32}", values["API_HASH"])
                or len(values["SESSION_REF"]) > 256
                or len(values["SESSION_CIPHERTEXT"]) > 16384
                or len(values["ENCRYPTION_KEY"]) != 44
            ):
                raise ValueError
            Fernet(values["ENCRYPTION_KEY"].encode("ascii"))
        except (ValueError, UnicodeError, PersonalTelegramTransportError):
            raise PersonalTelegramTransportError("cloud_binding_invalid") from None
        return cls(
            api_id=api_id,
            api_hash=values["API_HASH"],
            expected_owner_user_id=owner_id,
            session_ref=values["SESSION_REF"],
            ciphertext=values["SESSION_CIPHERTEXT"],
            encryption_key=values["ENCRYPTION_KEY"],
        )


@dataclass(frozen=True, slots=True, repr=False)
class StoredCloudSession:
    api_id: int
    owner_user_id: int
    session_ref: str
    session: str


def open_cloud_session(cipher: Fernet, ciphertext: bytes) -> StoredCloudSession:
    """Decode the common authenticated envelope, without resolving a caller's scope."""
    try:
        if not 0 < len(ciphertext) <= 16384:
            raise ValueError
        payload = json.loads(cipher.decrypt(ciphertext))
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ValueError
        api_id = payload.get("api_id")
        owner_id = payload.get("owner_user_id")
        ref = payload.get("session_ref")
        value = payload.get("session")
        if (
            type(api_id) is not int
            or not 0 < api_id < 2**31
            or type(owner_id) is not int
            or not 0 < owner_id < 2**63
            or not isinstance(ref, str)
            or len(ref) > 256
            or not isinstance(value, str)
            or not 0 < len(value) <= 4096
            or value != value.strip()
        ):
            raise ValueError
        _require_prefixed_ref(ref, "session_", "session_ref")
        return StoredCloudSession(api_id, owner_id, ref, value)
    except (ValueError, TypeError, UnicodeError, InvalidToken, PersonalTelegramTransportError):
        raise PersonalTelegramTransportError("cloud_session_unavailable") from None


class CloudEncryptedSessionSource:
    """Unseal only the configured owner/application/session inside this process."""

    def __init__(self, binding: CloudSessionBinding) -> None:
        self._binding = binding

    def load(self, *, session_ref: str) -> str:
        if session_ref != self._binding.session_ref:
            raise PersonalTelegramTransportError("cloud_session_scope_mismatch")
        try:
            value = open_cloud_session(
                Fernet(self._binding.encryption_key.encode("ascii")),
                self._binding.ciphertext.encode("ascii"),
            )
            if (
                value.api_id != self._binding.api_id
                or value.owner_user_id != self._binding.expected_owner_user_id
                or value.session_ref != session_ref
            ):
                raise ValueError
            return value.session
        except (ValueError, TypeError, UnicodeError):
            raise PersonalTelegramTransportError("cloud_session_unavailable") from None


def cloud_binding_enabled(environment: Mapping[str, str]) -> bool:
    return environment.get(PREFIX + "CLOUD_ENABLED", "").lower() == "true"


def inspect_cloud_session(environment: Mapping[str, str]) -> dict[str, object]:
    """Report custody only. Decryptability is not Telegram authorization."""
    configured = False
    error_code: str | None = None
    try:
        binding = CloudSessionBinding.from_environment(environment)
        CloudEncryptedSessionSource(binding).load(session_ref=binding.session_ref)
        configured = True
    except PersonalTelegramTransportError as exc:
        error_code = str(exc)
    return {
        "configured": configured,
        "authenticated": None,
        "runtime_connected": False,
        "send_enabled": False,
        "create_group_enabled": False,
        "last_successful_check": None,
        "error_code": error_code or "cloud_runtime_not_bound",
    }
