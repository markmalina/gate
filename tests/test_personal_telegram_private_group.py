"""Offline one-attempt private group checks; no Telegram network calls."""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from app.application.personal_telegram_transport import (
    PersonalTelegramPolicy,
    PersonalTelegramTransportError,
)
from app.personal_telegram.local_runtime import LocalEncryptedSessionSource
from app.personal_telegram.private_group import (
    CreatePrivateGroupCommand,
    CreatePrivateGroupLocalService,
    GroupOutcome,
    PrivateGroupReceipt,
    SqlitePrivateGroupJournal,
    TelethonPrivateGroupTransport,
)
from cryptography.fernet import Fernet

NOW = datetime(2026, 9, 23, 20, 30, tzinfo=UTC)
TOKEN = "owner-private-group-approval-synthetic-0001"
TITLE = "Точная закрытая группа"


def command(**changes: object) -> CreatePrivateGroupCommand:
    fields: dict[str, object] = dict(
        actor_ref="actor_mark",
        session_ref="session_synthetic_private",
        exact_title=TITLE,
        approval_token=TOKEN,
        idempotency_key="group_synthetic_private",
        now=NOW,
    )
    fields.update(changes)
    return CreatePrivateGroupCommand(**fields)  # type: ignore[arg-type]


def journal(tmp_path: Path) -> SqlitePrivateGroupJournal:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700, parents=True)
    key_file = directory / "key.enc"
    key_file.write_bytes(Fernet.generate_key())
    key_file.chmod(0o600)
    store = SqlitePrivateGroupJournal(directory / "groups.sqlite3", key_file=key_file)
    store.register_exact_approval(
        approval_token=TOKEN,
        actor_ref="actor_mark",
        session_ref="session_synthetic_private",
        exact_title=TITLE,
        expires_at=NOW + timedelta(minutes=10),
    )
    return store


def test_default_off_and_restart_replay_unknown(tmp_path: Path) -> None:
    class FakeTransport:
        calls = 0

        async def create(self, **_: object) -> PrivateGroupReceipt:
            self.calls += 1
            raise TimeoutError("synthetic provider ambiguity")

    store = journal(tmp_path)
    transport = FakeTransport()
    disabled = CreatePrivateGroupLocalService(
        policy=PersonalTelegramPolicy(), journal=store, transport=transport
    )
    with pytest.raises(PersonalTelegramTransportError, match="group_disabled"):
        asyncio.run(disabled.execute(command()))
    enabled = CreatePrivateGroupLocalService(
        policy=PersonalTelegramPolicy(create_group_enabled=True),
        journal=store,
        transport=transport,
    )
    first = asyncio.run(enabled.execute(command()))
    reopened = SqlitePrivateGroupJournal(
        tmp_path / "private" / "groups.sqlite3", key_file=tmp_path / "private" / "key.enc"
    )
    replay = asyncio.run(
        CreatePrivateGroupLocalService(
            policy=PersonalTelegramPolicy(create_group_enabled=True),
            journal=reopened,
            transport=transport,
        ).execute(command())
    )
    assert first.outcome is replay.outcome is GroupOutcome.UNKNOWN
    assert replay.idempotent_replay and transport.calls == 1
    db_bytes = (tmp_path / "private" / "groups.sqlite3").read_bytes()
    assert TOKEN.encode() not in db_bytes and TITLE.encode() not in db_bytes


def test_created_pin_is_encrypted_and_approval_cannot_be_reused(tmp_path: Path) -> None:
    store = journal(tmp_path)
    reserved = store.reserve(command())
    assert reserved.outcome is GroupOutcome.UNKNOWN and not reserved.idempotent_replay
    created = store.finalize(
        command(),
        PrivateGroupReceipt(GroupOutcome.CREATED, 321, 987654, True, True),
    )
    assert created.group_ref is not None and created.group_ref.startswith("group_")
    assert created.owner_verified and created.private_verified
    assert store.resolve_pin(created.group_ref) == (321, 987654)
    assert store.reserve(command()) == type(created)(
        GroupOutcome.CREATED, created.group_ref, True, True, True
    )
    with pytest.raises(PersonalTelegramTransportError, match="idempotency_conflict"):
        store.reserve(command(exact_title="Подменено"))
    with pytest.raises(PersonalTelegramTransportError, match="idempotency_conflict"):
        store.reserve(command(approval_token="different-approval-token-synthetic-0002"))
    with pytest.raises(PersonalTelegramTransportError, match="approval_unavailable"):
        store.reserve(command(idempotency_key="group_second_attempt"))
    raw = (tmp_path / "private" / "groups.sqlite3").read_bytes()
    assert b"987654" not in raw and TITLE.encode() not in raw and TOKEN.encode() not in raw


def test_revoked_and_expired_approvals_never_create(tmp_path: Path) -> None:
    store = journal(tmp_path)
    assert store.revoke_approval(TOKEN)
    with pytest.raises(PersonalTelegramTransportError, match="approval_unavailable"):
        store.reserve(command())
    second = journal(tmp_path / "second")
    with pytest.raises(PersonalTelegramTransportError, match="approval_expired"):
        second.reserve(command(now=NOW + timedelta(hours=1)))
    with pytest.raises(PersonalTelegramTransportError, match="approval_mismatch"):
        second.reserve(command(actor_ref="actor_other"))


def test_only_one_concurrent_create_attempt(tmp_path: Path) -> None:
    class BlockingTransport:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def create(self, **_: object) -> PrivateGroupReceipt:
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return PrivateGroupReceipt(GroupOutcome.CREATED, 123, 456, True, True)

    async def scenario() -> None:
        transport = BlockingTransport()
        service = CreatePrivateGroupLocalService(
            policy=PersonalTelegramPolicy(create_group_enabled=True),
            journal=journal(tmp_path),
            transport=transport,
        )
        first = asyncio.create_task(service.execute(command()))
        await transport.entered.wait()
        concurrent = await service.execute(command())
        assert concurrent.outcome is GroupOutcome.UNKNOWN and concurrent.idempotent_replay
        transport.release.set()
        created = await first
        assert created.outcome is GroupOutcome.CREATED and transport.calls == 1

    asyncio.run(scenario())


def test_telethon_creates_one_private_supergroup_without_invites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        instances: list[FakeClient] = []
        response: object = SimpleNamespace(
            chats=[
                SimpleNamespace(
                    id=321,
                    access_hash=654,
                    megagroup=True,
                    broadcast=False,
                    creator=True,
                    username=None,
                    usernames=None,
                    title=TITLE,
                    participants_count=1,
                )
            ]
        )

        def __init__(self, session: object, api_id: int, api_hash: str, **options: object):
            self.requests: list[object] = []
            self.options = options
            self.instances.append(self)

        async def connect(self) -> None:
            pass

        async def is_user_authorized(self) -> bool:
            return True

        async def get_me(self) -> SimpleNamespace:
            return SimpleNamespace(id=100)

        async def __call__(self, request: object) -> object:
            self.requests.append(request)
            return self.response

        async def disconnect(self) -> None:
            pass

    class CreateChannelRequest:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setitem(
        sys.modules,
        "telethon",
        types.SimpleNamespace(
            TelegramClient=FakeClient,
            functions=types.SimpleNamespace(
                channels=types.SimpleNamespace(CreateChannelRequest=CreateChannelRequest)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "telethon.sessions",
        types.SimpleNamespace(StringSession=lambda value: value),
    )
    source = cast(
        LocalEncryptedSessionSource,
        SimpleNamespace(load=lambda **_: "synthetic-session-material"),
    )
    transport = TelethonPrivateGroupTransport(
        source=source, api_id=42, api_hash="synthetic", expected_owner_user_id=100
    )
    result = asyncio.run(
        transport.create(session_ref="session_synthetic_private", exact_title=TITLE)
    )
    assert result == PrivateGroupReceipt(GroupOutcome.CREATED, 321, 654, True, True)
    client = FakeClient.instances[-1]
    assert len(client.requests) == 1
    assert client.requests[0].kwargs == {
        "title": TITLE,
        "about": "",
        "megagroup": True,
        "broadcast": False,
    }
    assert client.options["request_retries"] == 0
    assert client.options["auto_reconnect"] is False
    FakeClient.response = SimpleNamespace(chats=[])
    unknown = asyncio.run(
        transport.create(session_ref="session_synthetic_private", exact_title=TITLE)
    )
    assert unknown.outcome is GroupOutcome.UNKNOWN
