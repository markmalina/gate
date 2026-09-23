"""Synthetic proof that the local auth check cannot send or read dialogs."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from typing import cast

import pytest
from app.personal_telegram.auth_check import LocalAuthorizationStatus, check_local_authorization
from app.personal_telegram.local_runtime import LocalEncryptedSessionSource


def test_owner_authorization_reads_only_two_account_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        instances: list[Client] = []
        authorized = True
        owner_id = 42
        fail = False

        def __init__(self, session: object, api_id: int, api_hash: str, **options: object):
            self.calls: list[str] = []
            self.options = options
            self.instances.append(self)

        async def connect(self) -> None:
            self.calls.append("connect")

        async def is_user_authorized(self) -> bool:
            self.calls.append("authorized")
            if self.fail:
                raise TimeoutError("synthetic secret must not escape")
            return self.authorized

        async def get_me(self) -> SimpleNamespace:
            self.calls.append("get_me")
            return SimpleNamespace(id=self.owner_id)

        async def disconnect(self) -> None:
            self.calls.append("disconnect")

    monkeypatch.setitem(sys.modules, "telethon", types.SimpleNamespace(TelegramClient=Client))
    monkeypatch.setitem(
        sys.modules, "telethon.sessions", types.SimpleNamespace(StringSession=lambda x: x)
    )
    source = cast(
        LocalEncryptedSessionSource,
        SimpleNamespace(load=lambda **_: "synthetic-session-secret-not-for-output"),
    )

    def check() -> LocalAuthorizationStatus:
        return asyncio.run(
            check_local_authorization(
                source=source,
                session_ref="session_synthetic",
                api_id=123,
                api_hash="synthetic-api-secret",
                expected_owner_user_id=42,
            )
        )

    ready = check()
    assert ready.configured and ready.authenticated is True
    assert ready.probe_connected and not ready.runtime_connected
    assert ready.last_successful_check
    assert ready.send_enabled is False and ready.create_group_enabled is False
    assert Client.instances[-1].calls == ["connect", "authorized", "get_me", "disconnect"]
    assert Client.instances[-1].options["receive_updates"] is False
    assert Client.instances[-1].options["request_retries"] == 0
    serialized = ready.as_json()
    assert "42" not in serialized and "synthetic-api-secret" not in serialized
    assert "synthetic-session-secret" not in serialized
    assert json.loads(serialized)["error_code"] is None

    Client.owner_id = 99
    mismatch = check()
    assert mismatch.authenticated is False
    assert mismatch.error_code == "owner_mismatch"
    assert Client.instances[-1].calls == ["connect", "authorized", "get_me", "disconnect"]

    Client.authorized = False
    unauthorized = check()
    assert unauthorized.authenticated is False
    assert unauthorized.error_code == "session_unauthorized"
    assert Client.instances[-1].calls == ["connect", "authorized", "disconnect"]

    Client.authorized = True
    Client.fail = True
    unknown = check()
    assert unknown.authenticated is None
    assert unknown.error_code == "provider_check_unavailable"
    assert "synthetic secret" not in unknown.as_json()
