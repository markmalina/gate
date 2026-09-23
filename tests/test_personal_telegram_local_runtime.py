"""Offline checks of the real local adapter and its durable send boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from cryptography.fernet import Fernet

from app.application.personal_telegram_fence import (
    PersonalSendFenceCommand,
    PersonalSendFenceResult,
)
from app.application.personal_telegram_transport import (
    ExactSendCommand,
    PersonalTelegramPolicy,
    PersonalTelegramTransportError,
    ProviderOutcome,
    ProviderSendReceipt,
)
from app.personal_telegram.durable_send import (
    SendExactPersonalTelegramLocalService,
    SqlitePersonalTelegramSendJournal,
)
from app.personal_telegram.local_runtime import (
    LocalEncryptedSessionSource,
    PinnedUserPeer,
    TelethonPersonalTelegramTransport,
)

NOW = datetime(2026, 9, 23, 11, 30, tzinfo=UTC)
TOKEN = "synthetic-owner-token-not-a-real-secret-0001"
TEXT = "Точный тестовый текст."


def command(**changes: object) -> ExactSendCommand:
    fields: dict[str, object] = dict(
        actor_ref="actor_owner",
        session_ref="session_synthetic001",
        peer_ref="peer_synthetic001",
        exact_text=TEXT,
        approval_token=TOKEN,
        idempotency_key="send_synthetic001",
        now=NOW,
    )
    fields.update(changes)
    return ExactSendCommand(**fields)  # type: ignore[arg-type]


def fence_command() -> PersonalSendFenceCommand:
    return PersonalSendFenceCommand(
        draft_id=UUID("00000000-0000-0000-0000-000000000001"),
        account_id=UUID("00000000-0000-0000-0000-000000000002"),
        actor_ref="actor_owner",
        session_ref="session_synthetic001",
        peer_ref="peer_synthetic001",
        exact_text_digest=hashlib.sha256(TEXT.encode()).hexdigest(),
        approval_digest=hashlib.sha256(TOKEN.encode()).hexdigest(),
        idempotency_key="send_synthetic001",
        now=NOW,
    )


class SyntheticFence:
    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.outcome: str | None = None
        self.receipt_ref: str | None = None

    async def reserve(self, _command: PersonalSendFenceCommand) -> PersonalSendFenceResult:
        if self.refuse:
            raise RuntimeError("synthetic_fence_unavailable")
        replay = self.outcome is not None
        self.outcome = self.outcome or "unknown"
        return PersonalSendFenceResult(UUID(int=3), self.outcome, self.receipt_ref, replay)

    async def finalize(
        self,
        _command: PersonalSendFenceCommand,
        *,
        outcome: str,
        receipt_ref: str | None,
        external_effect_performed: bool | None,
    ) -> PersonalSendFenceResult:
        assert external_effect_performed is not None or outcome == "unknown"
        self.outcome, self.receipt_ref = outcome, receipt_ref
        return PersonalSendFenceResult(UUID(int=3), outcome, receipt_ref, False)


def journal(tmp_path: Path) -> SqlitePersonalTelegramSendJournal:
    store = SqlitePersonalTelegramSendJournal(tmp_path / "private" / "send.sqlite3")
    store.register_exact_approval(
        approval_token=TOKEN,
        actor_ref="actor_owner",
        session_ref="session_synthetic001",
        peer_ref="peer_synthetic001",
        exact_text=TEXT,
        expires_at=NOW + timedelta(minutes=10),
    )
    return store


class BlockingTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send_exact(self, **_: object) -> ProviderSendReceipt:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return ProviderSendReceipt(ProviderOutcome.SENT, "telegram_321", True)


def test_journal_commits_unknown_before_provider_and_replays_across_restart(tmp_path: Path) -> None:
    store = journal(tmp_path)
    reserved = store.reserve(command())
    assert not reserved.replay
    assert reserved.result.outcome is ProviderOutcome.UNKNOWN
    reopened = SqlitePersonalTelegramSendJournal(tmp_path / "private" / "send.sqlite3")
    replay = reopened.reserve(command())
    assert replay.replay and replay.result.outcome is ProviderOutcome.UNKNOWN
    with sqlite3.connect(tmp_path / "private" / "send.sqlite3") as db:
        attempt = db.execute("SELECT outcome FROM attempts").fetchone()
    assert attempt == ("unknown",)
    assert TOKEN not in (tmp_path / "private" / "send.sqlite3").read_bytes().decode("latin1")
    assert TEXT not in (tmp_path / "private" / "send.sqlite3").read_bytes().decode("latin1")


def test_only_one_concurrent_provider_attempt(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = journal(tmp_path)
        transport = BlockingTransport()
        service = SendExactPersonalTelegramLocalService(
            policy=PersonalTelegramPolicy(send_enabled=True),
            journal=store,
            transport=transport,
            shared_fence=SyntheticFence(),
        )
        first = asyncio.create_task(service.execute(command(), fence_command=fence_command()))
        await transport.entered.wait()
        concurrent = await service.execute(command(), fence_command=fence_command())
        assert concurrent.outcome is ProviderOutcome.UNKNOWN
        assert concurrent.idempotent_replay
        transport.release.set()
        sent = await first
        assert sent.outcome is ProviderOutcome.SENT
        assert sent.provider_receipt_ref == "telegram_321"
        assert transport.calls == 1
        reopened = SqlitePersonalTelegramSendJournal(tmp_path / "private" / "send.sqlite3")
        replay = await SendExactPersonalTelegramLocalService(
            policy=PersonalTelegramPolicy(send_enabled=True),
            journal=reopened,
            transport=transport,
            shared_fence=service._shared_fence,
        ).execute(command(), fence_command=fence_command())
        assert replay.outcome is ProviderOutcome.SENT
        assert replay.idempotent_replay and transport.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"session_ref": "session_other"}, "session_mismatch"),
        ({"peer_ref": "peer_other"}, "peer_mismatch"),
        ({"exact_text": "Подмена."}, "exact_text_mismatch"),
        ({"actor_ref": "actor_other"}, "actor_mismatch"),
        ({"now": NOW + timedelta(hours=1)}, "approval_expired"),
    ],
)
def test_approval_scope_is_exact(tmp_path: Path, changes: dict[str, object], code: str) -> None:
    store = journal(tmp_path)
    with pytest.raises(PersonalTelegramTransportError, match=f"personal_telegram_{code}"):
        store.reserve(command(**changes))


def test_revocation_and_idempotency_conflict(tmp_path: Path) -> None:
    store = journal(tmp_path)
    assert store.revoke_approval(TOKEN)
    with pytest.raises(PersonalTelegramTransportError, match="approval_consumed"):
        store.reserve(command())
    other = SqlitePersonalTelegramSendJournal(tmp_path / "new.sqlite3")
    other.register_exact_approval(
        approval_token=TOKEN,
        actor_ref="actor_owner",
        session_ref="session_synthetic001",
        peer_ref="peer_synthetic001",
        exact_text=TEXT,
        expires_at=NOW + timedelta(minutes=10),
    )
    other.reserve(command())
    with pytest.raises(PersonalTelegramTransportError, match="idempotency_conflict"):
        other.reserve(command(exact_text="Подмена."))


def test_default_off_and_ambiguous_failure_remain_terminal(tmp_path: Path) -> None:
    class FailingTransport:
        calls = 0

        async def send_exact(self, **_: object) -> ProviderSendReceipt:
            self.calls += 1
            raise TimeoutError("synthetic timeout containing possible provider data")

    store = journal(tmp_path)
    transport = FailingTransport()
    service = SendExactPersonalTelegramLocalService(
        policy=PersonalTelegramPolicy(),
        journal=store,
        transport=transport,
        shared_fence=SyntheticFence(),
    )
    with pytest.raises(PersonalTelegramTransportError, match="send_disabled"):
        asyncio.run(service.execute(command(), fence_command=fence_command()))
    shared = SyntheticFence()
    enabled = SendExactPersonalTelegramLocalService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        journal=store,
        transport=transport,
        shared_fence=shared,
    )
    first = asyncio.run(enabled.execute(command(), fence_command=fence_command()))
    replay = asyncio.run(enabled.execute(command(), fence_command=fence_command()))
    assert first.outcome is replay.outcome is ProviderOutcome.UNKNOWN
    assert replay.idempotent_replay and transport.calls == 1


def test_shared_fence_refusal_prevents_provider_and_local_reservation(tmp_path: Path) -> None:
    transport = BlockingTransport()
    store = journal(tmp_path)
    service = SendExactPersonalTelegramLocalService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        journal=store,
        transport=transport,
        shared_fence=SyntheticFence(refuse=True),
    )
    with pytest.raises(RuntimeError, match="synthetic_fence_unavailable"):
        asyncio.run(service.execute(command(), fence_command=fence_command()))
    assert transport.calls == 0
    with sqlite3.connect(tmp_path / "private" / "send.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM attempts").fetchone() == (0,)


def enrollment(tmp_path: Path) -> LocalEncryptedSessionSource:
    directory = tmp_path / "enrollment"
    directory.mkdir(mode=0o700)
    key = Fernet.generate_key()
    key_file = directory / "key.enc"
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    receipt_file = directory / "session_synthetic001.receipt.json"
    receipt_file.write_text(
        json.dumps(
            {
                "session_ref": "session_synthetic001",
                "secret_store_class": "posix_owner_only_fernet_file",
                "send_enabled": False,
                "read_dialogs_enabled": False,
                "contact_sync_enabled": False,
            }
        )
    )
    receipt_file.chmod(0o600)
    ciphertext = directory / "session_synthetic001.session.enc"
    ciphertext.write_bytes(Fernet(key).encrypt(b"synthetic-session-material"))
    ciphertext.chmod(0o600)
    return LocalEncryptedSessionSource(
        key_file=key_file, receipt_file=receipt_file, require_windows=False
    )


def test_local_source_requires_original_host_and_rejects_tampering(tmp_path: Path) -> None:
    source = enrollment(tmp_path)
    assert source.load(session_ref="session_synthetic001") == "synthetic-session-material"
    if sys.platform != "win32":
        restricted = LocalEncryptedSessionSource(
            key_file=source._key_file, receipt_file=source._receipt_file
        )
        with pytest.raises(PersonalTelegramTransportError, match="original_windows_host_required"):
            restricted.load(session_ref="session_synthetic001")
        source._key_file.chmod(0o644)
        with pytest.raises(PersonalTelegramTransportError, match="local_session_unavailable"):
            source.load(session_ref="session_synthetic001")


def test_telethon_raw_send_uses_exact_pin_and_single_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UpdateMessageID:
        random_id = 42
        id = 654

    class TelegramClient:
        instances: list[TelegramClient] = []
        authorized = True
        owner_id = 123
        error: Exception | None = None
        missing_confirmation = False

        def __init__(self, session: object, api_id: int, api_hash: str, **options: object):
            self.options = options
            self.requests: list[object] = []
            self.connected = False
            self.disconnected = False
            self.instances.append(self)

        async def connect(self) -> None:
            self.connected = True

        async def is_user_authorized(self) -> bool:
            return self.authorized

        async def get_me(self) -> SimpleNamespace:
            return SimpleNamespace(id=self.owner_id)

        async def __call__(self, request: object) -> SimpleNamespace:
            self.requests.append(request)
            if self.error is not None:
                raise self.error
            if self.missing_confirmation:
                return SimpleNamespace(updates=[])
            return SimpleNamespace(updates=[UpdateMessageID()])

        async def disconnect(self) -> None:
            self.disconnected = True

    telethon = types.ModuleType("telethon")
    sessions = types.ModuleType("telethon.sessions")
    sessions.StringSession = lambda value: value  # type: ignore[attr-defined]
    telethon.TelegramClient = TelegramClient  # type: ignore[attr-defined]
    telethon.functions = SimpleNamespace(
        messages=SimpleNamespace(  # type: ignore[attr-defined]
            SendMessageRequest=lambda **kwargs: SimpleNamespace(**kwargs)
        )
    )
    telethon.types = SimpleNamespace(  # type: ignore[attr-defined]
        InputPeerUser=lambda user_id, access_hash: (user_id, access_hash)
    )
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)

    class Peers:
        def resolve(self, *, session_ref: str, peer_ref: str) -> PinnedUserPeer:
            assert session_ref == "session_synthetic001"
            return PinnedUserPeer(peer_ref, 456, 789)

    transport = TelethonPersonalTelegramTransport(
        source=enrollment(tmp_path),
        peers=Peers(),
        api_id=111,
        api_hash="synthetic-hash",
        expected_owner_user_id=123,
    )
    receipt = asyncio.run(
        transport.send_exact(
            session_ref="session_synthetic001",
            peer_ref="peer_synthetic001",
            exact_text=TEXT,
            deterministic_random_id=42,
        )
    )
    assert receipt == ProviderSendReceipt(ProviderOutcome.SENT, "telegram_654", True)
    client = TelegramClient.instances[0]
    assert client.connected and client.disconnected
    assert len(client.requests) == 1
    assert client.requests[0].peer == (456, 789)
    assert client.requests[0].message == TEXT
    assert client.requests[0].random_id == 42
    assert client.options["request_retries"] == 0
    assert client.options["auto_reconnect"] is False
    assert client.options["receive_updates"] is False
    assert client.options["flood_sleep_threshold"] == 0

    def attempt() -> ProviderSendReceipt:
        return asyncio.run(
            transport.send_exact(
                session_ref="session_synthetic001",
                peer_ref="peer_synthetic001",
                exact_text=TEXT,
                deterministic_random_id=42,
            )
        )

    TelegramClient.owner_id = 999
    assert attempt() == ProviderSendReceipt(ProviderOutcome.REJECTED, None, False)
    assert TelegramClient.instances[-1].requests == []
    TelegramClient.owner_id = 123
    TelegramClient.authorized = False
    assert attempt() == ProviderSendReceipt(ProviderOutcome.REJECTED, None, False)
    assert TelegramClient.instances[-1].requests == []
    TelegramClient.authorized = True
    for failure in (TimeoutError("unknown"), RuntimeError("FLOOD_WAIT_60")):
        TelegramClient.error = failure
        assert attempt() == ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
        assert len(TelegramClient.instances[-1].requests) == 1
    TelegramClient.error = None
    TelegramClient.missing_confirmation = True
    assert attempt() == ProviderSendReceipt(ProviderOutcome.UNKNOWN, None, None)
