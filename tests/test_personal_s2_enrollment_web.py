import asyncio
import logging
from pathlib import Path
from time import monotonic, sleep

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

pytest.importorskip("telethon")

from app.personal_telegram import s2_enrollment_web as web
from app.personal_telegram.cloud_worker import TelegramApplication
from app.personal_telegram.s2_enrollment_web import (
    ENVELOPE,
    Config,
    app,
    save_envelope,
    verify_owner,
    verify_saved_session,
)
from telethon.tl.types import User
from telethon.tl.types.auth import SentCode, SentCodeTypeApp


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        TelegramApplication(17, "a" * 32),
        "70000000000",
        "Z" * 43,
        Fernet(Fernet.generate_key()),
        "https://fixture.up.railway.app",
        tmp_path,
    )


def test_actual_telethon_owner_fields_and_phone_pinning(config: Config) -> None:
    account = User(id=123, is_self=True, phone=config.phone)
    assert verify_owner(account, config.phone) == 123
    with pytest.raises(ValueError, match="owner_mismatch"):
        verify_owner(User(id=123, is_self=True, phone="375000000000"), config.phone)
    with pytest.raises(ValueError, match="owner_mismatch"):
        verify_owner(User(id=123, phone=config.phone), config.phone)


def test_envelope_is_encrypted_and_a_second_login_cannot_replace_it(config: Config) -> None:
    save_envelope(config, owner_id=123, session="fake-session-one")
    file = config.vault / ENVELOPE
    payload = file.read_bytes()
    assert b"fake-session-one" not in payload
    assert b"fake-session-one" in config.cipher.decrypt(payload)
    assert file.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_envelope(config, owner_id=123, session="fake-session-two")
    assert file.read_bytes() == payload


def test_browser_controls_are_owner_only(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Config, "read", staticmethod(lambda: config))
    with TestClient(app, base_url=config.origin) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").headers["referrer-policy"] == "same-origin"
        assert client.get("/qr").status_code == 404
        assert client.post("/start").status_code == 403
        payload = {"access": f" {config.access}\n"}
        assert (
            client.post(
                "/unlock",
                data=payload,
                headers={
                    "Origin": "https://elsewhere.invalid",
                },
            ).status_code
            == 403
        )
        rejected = client.post("/unlock", data=payload, headers={"Origin": "null"})
        assert rejected.status_code == 403
        assert "Обнови страницу Gate" in rejected.text
        assert "set-cookie" not in rejected.headers
        response = client.post(
            "/unlock",
            data=payload,
            headers={"Origin": config.origin},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert config.access not in response.text
        assert "Показать QR" in client.get("/").text
        assert "Войти по коду" in client.get("/").text
        assert client.get("/qr").headers["cache-control"] == "no-store, private"


def test_lifecycle_canary_runs_only_from_explicit_startup_grant(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = []

    async def canary(run_id, environment):
        calls.append((run_id, environment["GATE_S2_RUNTIME_CANARY"]))
        return {
            "state": "verified_and_disconnected",
            "overlap_rejected": True,
            "database_loss_failed_closed": True,
            "telegram_owner_checks": 2,
            "outbound_messages": 0,
        }

    monkeypatch.setattr(Config, "read", staticmethod(lambda: config))
    monkeypatch.setattr(web, "run_s2_lifecycle_canary", canary)
    monkeypatch.setenv("GATE_S2_RUNTIME_CANARY", "fixture-lifecycle")
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(app, base_url=config.origin) as client:
            assert client.get("/health").is_success
    assert calls == [("fixture-lifecycle", "fixture-lifecycle")]
    assert "s2_ru_lifecycle_verified_twice_and_disconnected" in caplog.text


def test_phone_only_enrollment_uses_owner_number_and_encrypted_session(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Session:
        def save(self):
            return "fake-mobile-session"

    class Telegram:
        session = Session()

        async def connect(self):
            pass

        async def send_code_request(self, phone):
            calls.append(("request", phone))
            return SentCode(SentCodeTypeApp(5), "fake-challenge")

        async def sign_in(self, *, phone, code, phone_code_hash):
            calls.append(("submit", phone, code, phone_code_hash))

        async def get_me(self):
            return User(id=123, is_self=True, phone=config.phone)

        async def disconnect(self):
            pass

    def wait_for_page(client: TestClient, phrase: str) -> None:
        until = monotonic() + 4
        while monotonic() < until:
            if phrase in client.get("/").text:
                return
            sleep(0.01)
        pytest.fail(f"Missing expected page state: {phrase}")

    monkeypatch.setattr(Config, "read", staticmethod(lambda: config))
    monkeypatch.setattr(web, "TelegramClient", lambda *args, **kwargs: Telegram())
    headers = {"Origin": config.origin}
    with TestClient(app, base_url=config.origin) as client:
        assert client.post("/start-code", headers=headers).status_code == 403
        assert calls == []
        assert client.post("/unlock", data={"access": config.access}, headers=headers).is_success
        assert (
            client.post("/start-code", headers={"Origin": "https://wrong.invalid"}).status_code
            == 403
        )
        assert (
            client.post("/start-code", headers=headers, follow_redirects=False).status_code == 303
        )
        wait_for_page(client, "Telegram отправил код в приложение")
        assert (
            client.post("/start-code", headers=headers, follow_redirects=False).status_code == 303
        )
        assert client.post("/code", data={"code": "12"}, headers=headers).status_code == 400
        assert calls == [("request", f"+{config.phone}")]
        assert (
            client.post(
                "/code", data={"code": "01234"}, headers=headers, follow_redirects=False
            ).status_code
            == 303
        )
        wait_for_page(client, "Российский аккаунт подтверждён Telegram")
    assert calls[-1] == ("submit", f"+{config.phone}", "01234", "fake-challenge")
    assert b"fake-mobile-session" in config.cipher.decrypt((config.vault / ENVELOPE).read_bytes())


@pytest.mark.parametrize("reopened_owner", [123, 124])
def test_saved_session_probe_has_one_client_and_requires_saved_owner(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reopened_owner: int,
) -> None:
    save_envelope(config, owner_id=123, session="fake-saved-session")
    original = (config.vault / ENVELOPE).read_bytes()
    calls = []

    class Telegram:
        def __init__(self, session, *args, **kwargs):
            assert session == "fake-saved-session"
            assert kwargs["receive_updates"] is False
            calls.append("client")

        async def connect(self):
            calls.append("connect")

        async def get_me(self):
            calls.append("get_me")
            return User(id=reopened_owner, is_self=True, phone=config.phone)

        async def disconnect(self):
            calls.append("disconnect")

    monkeypatch.setattr(web, "StringSession", lambda value: value)
    monkeypatch.setattr(web, "TelegramClient", Telegram)

    async def check() -> None:
        await asyncio.gather(
            verify_saved_session(config, "fixture-probe"),
            verify_saved_session(config, "fixture-probe"),
        )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        asyncio.run(check())
    assert calls == ["client", "connect", "get_me", "disconnect"]
    assert (config.vault / ENVELOPE).read_bytes() == original
    assert ("s2_ru_saved_session_verified_and_disconnected" in caplog.text) is (
        reopened_owner == 123
    )
