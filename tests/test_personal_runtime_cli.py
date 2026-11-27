"""Owner-only cloud cutover contracts with no Telegram or privileged PostgreSQL use."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.personal_telegram.cloud_session import PREFIX, SCHEMA
from app.personal_telegram.command_scope import CommandRuntimeError, utc_now
from app.personal_telegram.command_store import (
    CommandPayloadCipher,
    PersonalRuntimeBase,
    RuntimeBindingRow,
    SqlCommandStore,
)
from app.personal_telegram.runtime_cli import (
    RuntimeSettings,
    import_existing,
    import_s2_volume,
    inspect_runtime,
    require_s2_database,
    run,
)
from app.personal_telegram.s2_session_vault import ENVELOPE, SESSION_REF, read_s2_session
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ALIAS_API_ID = "GATE_PERSONAL_TELEGRAM_API_ID"
ALIAS_API_HASH = "GATE_PERSONAL_TELEGRAM_API_HASH"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def isolated():
    config = RuntimeSettings(
        database_url="unused",
        workspace="work-owner",
        owner="verified-owner",
        binding_id=uuid4(),
        session_key=Fernet.generate_key().decode(),
        command_key=Fernet.generate_key().decode(),
        expected_provider_owner=40,
        **{ALIAS_API_ID: 100, ALIAS_API_HASH: "a" * 32},
    )
    env_key = Fernet.generate_key()
    payload = json.dumps(
        {
            "schema": SCHEMA,
            "api_id": config.api_id,
            "owner_user_id": 40,
            "session_ref": "session_fixture_40",
            "session": "synthetic-private-session",
        }
    )
    environment = {
        PREFIX + "API_ID": "100",
        PREFIX + "API_HASH": "a" * 32,
        PREFIX + "OWNER_USER_ID": "40",
        PREFIX + "SESSION_REF": "session_fixture_40",
        PREFIX + "SESSION_CIPHERTEXT": Fernet(env_key).encrypt(payload.encode()).decode(),
        PREFIX + "ENCRYPTION_KEY": env_key.decode(),
    }
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PersonalRuntimeBase.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False), config, environment
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_run_off_has_no_configuration_or_external_effect():
    assert await run("run", 60, {}) == {"account": "RU", "state": "disabled"}


@pytest.mark.anyio
async def test_imported_session_and_current_owner_check_are_different(isolated):
    sessions, config, environment = isolated
    assert (await inspect_runtime(sessions, config))["error_code"] == "ru_binding_not_found"
    assert (await import_existing(sessions, config, environment))["authenticated"] is None
    async with sessions() as session:
        row = await session.get(RuntimeBindingRow, config.binding_id)
        assert b"synthetic-private-session" not in row.ciphertext
        initial_ciphertext = row.ciphertext
    assert (await import_existing(sessions, config, environment))["state"] == "already_prepared"
    async with sessions() as session:
        assert (
            await session.get(RuntimeBindingRow, config.binding_id)
        ).ciphertext == initial_ciphertext

    store = SqlCommandStore(
        sessions, scope=config.scope(), payload_cipher=CommandPayloadCipher(Fernet.generate_key())
    )
    assert not (await inspect_runtime(sessions, config))["runtime_connected"]
    now, ttl = utc_now(), timedelta(seconds=30)
    for generation in (1, 2):
        token = await store.acquire_owner(now=now, ttl=ttl)
        assert not (await inspect_runtime(sessions, config))["runtime_connected"]
        await store.renew_owner(token, now=now, ttl=ttl, health="ready")
        assert not (await inspect_runtime(sessions, config))["runtime_connected"]
        await store.renew_owner(token, now=now, ttl=ttl, health="ready", verified=True)
        status = await inspect_runtime(sessions, config)
        assert status["authenticated"] and status["runtime_connected"]
        assert status["lease_generation"] == generation
        assert status["last_successful_check"] == now.isoformat()
        assert status["send_enabled"] is False
        await store.release_disconnected_owner(token)
        assert not (await inspect_runtime(sessions, config))["runtime_connected"]


@pytest.mark.anyio
async def test_import_cannot_replace_other_owner_or_synthetic_binding(isolated):
    sessions, config, environment = isolated
    with pytest.raises(CommandRuntimeError, match="ru_session_identity_mismatch"):
        await import_existing(
            sessions, config.model_copy(update={"expected_provider_owner": 41}), environment
        )
    await import_existing(sessions, config, environment)
    async with sessions.begin() as session:
        row = await session.get(RuntimeBindingRow, config.binding_id)
        row.custody_state = "synthetic"
        row.ciphertext = b""
    with pytest.raises(CommandRuntimeError, match="ru_binding_conflict"):
        await import_existing(sessions, config, environment)
    assert not (await inspect_runtime(sessions, config))["configured"]


@pytest.mark.anyio
async def test_volume_import_is_encrypted_idempotent_and_refuses_other_sessions(
    isolated,
    tmp_path: Path,
):
    sessions, config, _ = isolated
    vault_cipher = Fernet(Fernet.generate_key())
    source = tmp_path / ENVELOPE

    def seal(session: str) -> None:
        payload = {
            "schema": SCHEMA,
            "api_id": 100,
            "owner_user_id": 40,
            "session_ref": SESSION_REF,
            "session": session,
        }
        source.write_bytes(vault_cipher.encrypt(json.dumps(payload).encode()))

    seal("fixture-volume-session")
    snapshot = read_s2_session(tmp_path, vault_cipher, api_id=100)
    assert "fixture-volume-session" not in repr(snapshot)
    assert (await import_s2_volume(sessions, config, snapshot))["state"] == "prepared"
    assert (await import_s2_volume(sessions, config, snapshot))["state"] == "already_prepared"
    status = await inspect_runtime(sessions, config)
    assert status["configured"] and not status["runtime_connected"]
    async with sessions() as session:
        row = await session.get(RuntimeBindingRow, config.binding_id)
        initial_ciphertext = row.ciphertext
        assert b"fixture-volume-session" not in row.ciphertext
    seal("other-volume-session")
    with pytest.raises(CommandRuntimeError, match="ru_binding_session_conflict"):
        await import_s2_volume(
            sessions, config, read_s2_session(tmp_path, vault_cipher, api_id=100)
        )
    with pytest.raises(PersonalTelegramTransportError, match="s2_vault_identity_mismatch"):
        read_s2_session(tmp_path, vault_cipher, api_id=101)
    async with sessions() as session:
        assert (
            await session.get(RuntimeBindingRow, config.binding_id)
        ).ciphertext == initial_ciphertext
    source.write_bytes(b"malformed")
    with pytest.raises(PersonalTelegramTransportError, match="cloud_session_unavailable"):
        read_s2_session(tmp_path, vault_cipher, api_id=100)
    source.unlink()
    other = tmp_path / "other"
    other.write_bytes(b"not a followable file")
    source.symlink_to(other)
    with pytest.raises(PersonalTelegramTransportError, match="s2_vault_unavailable"):
        read_s2_session(tmp_path, vault_cipher, api_id=100)


def test_s2_database_scope_refuses_public_hosts_and_other_databases():
    exact = "postgresql+asyncpg://gate_s2:test@gate-ru-store.railway.internal:5432/gate_s2_ru"
    require_s2_database(exact)
    for dsn in (
        exact.replace("gate-ru-store.railway.internal", "elsewhere.invalid"),
        exact.replace("/gate_s2_ru", "/other"),
        exact + "?host=elsewhere.invalid",
    ):
        with pytest.raises(CommandRuntimeError, match="s2_runtime_database_scope_mismatch"):
            require_s2_database(dsn)
