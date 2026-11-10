"""Cloud custody, scope and no-network defaults using synthetic session material."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from app.application.personal_telegram_status import personal_telegram_server_status
from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.personal_telegram.cloud_check_cli import check_cloud_session
from app.personal_telegram.cloud_session import (
    PREFIX,
    SCHEMA,
    CloudEncryptedSessionSource,
    CloudSessionBinding,
    inspect_cloud_session,
)
from cryptography.fernet import Fernet


def environment(**changes: object) -> dict[str, str]:
    key = Fernet.generate_key()
    payload = {
        "schema": SCHEMA,
        "api_id": 123,
        "owner_user_id": 42,
        "session_ref": "session_synthetic_cloud",
        "session": "synthetic-session-material",
    }
    payload.update(changes)
    return {
        PREFIX + "CLOUD_ENABLED": "true",
        PREFIX + "API_ID": "123",
        PREFIX + "API_HASH": "a" * 32,
        PREFIX + "OWNER_USER_ID": "42",
        PREFIX + "SESSION_REF": "session_synthetic_cloud",
        PREFIX + "SESSION_CIPHERTEXT": Fernet(key).encrypt(json.dumps(payload).encode()).decode(),
        PREFIX + "ENCRYPTION_KEY": key.decode(),
    }


def test_offline_inspection_cannot_claim_authorization_or_delivery() -> None:
    env = environment()
    status = personal_telegram_server_status(env)
    assert status["configured"] is True
    assert status["authenticated"] is None
    assert status["runtime_connected"] is False
    assert status["send_enabled"] is False
    assert status["create_group_enabled"] is False
    assert status["last_successful_check"] is None
    assert status["error_code"] == "cloud_runtime_not_bound"
    serialized = json.dumps(status)
    for name in [
        "API_HASH", "OWNER_USER_ID", "SESSION_REF", "SESSION_CIPHERTEXT", "ENCRYPTION_KEY"
    ]:
        assert env[PREFIX + name] not in serialized
    assert "synthetic-session-material" not in serialized
    binding = CloudSessionBinding.from_environment(env)
    assert env[PREFIX + "API_HASH"] not in repr(binding)
    assert "synthetic_cloud" not in repr(binding)


@pytest.mark.parametrize("field", ["API_ID", "API_HASH", "OWNER_USER_ID", "SESSION_REF",
                                   "SESSION_CIPHERTEXT", "ENCRYPTION_KEY"])
def test_missing_field_fails_closed(field: str) -> None:
    env = environment()
    del env[PREFIX + field]
    status = inspect_cloud_session(env)
    assert status["configured"] is False
    assert status["error_code"] == "cloud_binding_incomplete"


@pytest.mark.parametrize("changes", [
    {"owner_user_id": 99}, {"api_id": 321}, {"session_ref": "session_other"},
    {"schema": "wrong"}, {"session": ""}, {"session": " padded "}, {"owner_user_id": True},
])
def test_encrypted_scope_and_material_must_match(changes: dict[str, object]) -> None:
    status = inspect_cloud_session(environment(**changes))
    assert status["configured"] is False
    assert status["error_code"] == "cloud_session_unavailable"


def test_ciphertext_tampering_wrong_key_and_requested_session_are_rejected() -> None:
    env = environment()
    source = CloudEncryptedSessionSource(CloudSessionBinding.from_environment(env))
    with pytest.raises(PersonalTelegramTransportError, match="cloud_session_scope_mismatch"):
        source.load(session_ref="session_other")
    env[PREFIX + "ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    assert inspect_cloud_session(env)["error_code"] == "cloud_session_unavailable"
    env = environment()
    env[PREFIX + "SESSION_CIPHERTEXT"] = "invalid-ciphertext"
    assert inspect_cloud_session(env)["error_code"] == "cloud_session_unavailable"


@pytest.mark.parametrize("field,value", [
    ("API_ID", "-1"), ("OWNER_USER_ID", "0"), ("OWNER_USER_ID", str(2**63)),
    ("API_HASH", "invalid"), ("ENCRYPTION_KEY", "invalid"), ("SESSION_REF", "../unsafe"),
])
def test_malformed_binding_is_redacted(field: str, value: str) -> None:
    env = environment()
    env[PREFIX + field] = value
    status = inspect_cloud_session(env)
    assert status["configured"] is False
    assert status["error_code"] == "cloud_binding_invalid"


def test_live_flag_and_cloud_enable_are_both_required_for_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        calls: list[str] = []
        owner_id = 42

        def __init__(self, *_args: object, **options: object) -> None:
            self.calls.append("construct")
            assert options["receive_updates"] is False
            assert options["request_retries"] == 0

        async def connect(self) -> None:
            self.calls.append("connect")

        async def is_user_authorized(self) -> bool:
            self.calls.append("authorization")
            return True

        async def get_me(self) -> SimpleNamespace:
            self.calls.append("get_me")
            return SimpleNamespace(id=self.owner_id)

        async def disconnect(self) -> None:
            self.calls.append("disconnect")

    monkeypatch.setitem(sys.modules, "telethon", SimpleNamespace(TelegramClient=Client))
    monkeypatch.setitem(
        sys.modules, "telethon.sessions", SimpleNamespace(StringSession=lambda x: x)
    )
    env = environment()
    offline = asyncio.run(check_cloud_session(env))
    assert offline["configured"] is True
    assert offline["authenticated"] is None
    assert not Client.calls
    env[PREFIX + "CLOUD_ENABLED"] = "false"
    denied = asyncio.run(check_cloud_session(env, live_readonly=True))
    assert denied["error_code"] == "cloud_binding_disabled"
    assert not Client.calls
    env[PREFIX + "CLOUD_ENABLED"] = "true"
    result = asyncio.run(check_cloud_session(env, live_readonly=True))
    assert result["authenticated"] is True
    assert result["probe_connected"] is True
    assert result["runtime_connected"] is False
    assert result["send_enabled"] is False
    assert Client.calls == ["construct", "connect", "authorization", "get_me", "disconnect"]
    Client.owner_id = 99
    result = asyncio.run(check_cloud_session(env, live_readonly=True))
    assert result["authenticated"] is False
    assert result["error_code"] == "owner_mismatch"


def test_cloud_disabled_preserves_legacy_status_and_cannot_activate_send() -> None:
    env = environment()
    env[PREFIX + "CLOUD_ENABLED"] = "false"
    result = personal_telegram_server_status(env)
    assert result["configured"] is None
    assert result["error_code"] == "local_session_not_bound"
    assert result["send_enabled"] is False
