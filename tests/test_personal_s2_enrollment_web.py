from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

pytest.importorskip("telethon")

from app.personal_telegram.cloud_worker import TelegramApplication
from app.personal_telegram.s2_enrollment_web import (
    ENVELOPE,
    Config,
    app,
    save_envelope,
    verify_owner,
)
from telethon.tl.types import User


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
        assert client.get("/qr").status_code == 404
        assert client.post("/start").status_code == 403
        payload = {"access": config.access}
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
        assert client.get("/qr").headers["cache-control"] == "no-store, private"
