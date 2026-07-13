"""Read-only Telegram contact synchronizer tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app.contact_sync.gate_client import GatePublishError, GateSnapshotPublisher
from app.contact_sync.models import (
    ContactReadResult,
    ContactSnapshotItem,
    SavedPhoneContact,
    SnapshotImportCounts,
    TelegramContactUser,
)
from app.contact_sync.service import ContactSyncService, build_snapshot
from app.contact_sync.telethon_reader import TelethonContactReader, TelethonRuntime


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeReader:
    def __init__(self, result: ContactReadResult) -> None:
        self.result = result
        self.calls = 0

    async def read_contacts(self) -> ContactReadResult:
        self.calls += 1
        return self.result


class RecordingPublisher:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, tuple[ContactSnapshotItem, ...]]] = []

    async def publish(
        self,
        *,
        account_alias: str,
        revision: str,
        contacts: tuple[ContactSnapshotItem, ...],
    ) -> SnapshotImportCounts:
        self.requests.append((account_alias, revision, contacts))
        return SnapshotImportCounts(
            received=len(contacts),
            created=len(contacts),
            updated=0,
            unchanged=0,
        )


def test_build_snapshot_uses_saved_label_and_account_phone_match() -> None:
    snapshot = build_snapshot(
        [
            SavedPhoneContact("+375 29 000-00-01", "Old", saved_at=1),
            SavedPhoneContact("375290000001", "Saved", "Name", saved_at=2),
            SavedPhoneContact("+7 999 000-00-02", "Unmatched"),
        ],
        [
            TelegramContactUser("42", "+375290000001", "@person"),
            TelegramContactUser("43", "+1 555 000-00-03", "other"),
        ],
    )

    assert snapshot == (
        ContactSnapshotItem(
            telegram_user_id="42",
            display_name="Saved Name",
            username="person",
        ),
    )


@pytest.mark.anyio
async def test_sync_chunks_large_snapshot_with_deterministic_revisions() -> None:
    saved = tuple(
        SavedPhoneContact(str(10_000 + index), f"Saved {index}")
        for index in range(1001)
    )
    users = tuple(
        TelegramContactUser(str(index + 1), str(10_000 + index), f"user{index}")
        for index in reversed(range(1001))
    )
    source = ContactReadResult(saved_contacts=saved, telegram_users=users)
    first_publisher = RecordingPublisher()
    first = await ContactSyncService(FakeReader(source), first_publisher).execute(
        account_alias=" telegram_mark_by "
    )
    second_publisher = RecordingPublisher()
    await ContactSyncService(FakeReader(source), second_publisher).execute(
        account_alias="telegram_mark_by"
    )

    assert first.account_alias == "telegram_mark_by"
    assert first.saved_contacts == 1001
    assert first.matched_contacts == 1001
    assert first.skipped_contacts == 0
    assert first.chunks == 2
    assert first.import_counts.created == 1001
    assert [len(request[2]) for request in first_publisher.requests] == [1000, 1]
    assert [request[1] for request in first_publisher.requests] == [
        request[1] for request in second_publisher.requests
    ]
    assert all(len(request[1]) <= 128 for request in first_publisher.requests)


@pytest.mark.anyio
async def test_empty_match_does_not_call_gate() -> None:
    publisher = RecordingPublisher()
    result = await ContactSyncService(
        FakeReader(
            ContactReadResult(
                saved_contacts=(SavedPhoneContact("111", "Private label"),),
                telegram_users=(TelegramContactUser("9", "222"),),
            )
        ),
        publisher,
    ).execute(account_alias="telegram_mark_ru")

    assert result.matched_contacts == 0
    assert result.skipped_contacts == 1
    assert result.chunks == 0
    assert publisher.requests == []


@pytest.mark.anyio
async def test_gate_publisher_sends_protected_snapshot_and_parses_counts() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "account_alias": "telegram_mark_ru",
                "revision_sha256": "a" * 64,
                "counts": {
                    "received": 1,
                    "created": 0,
                    "updated": 1,
                    "unchanged": 0,
                },
            },
        )

    publisher = GateSnapshotPublisher(
        base_url="http://test",
        bearer_token="secret-token",
        transport=httpx.MockTransport(handler),
    )
    counts = await publisher.publish(
        account_alias="telegram_mark_ru",
        revision="revision-1",
        contacts=(ContactSnapshotItem("42", "Private label", "person"),),
    )

    assert counts.updated == 1
    assert captured is not None
    assert captured.url.path == "/admin/contact-snapshots"
    assert captured.headers["Authorization"] == "Bearer secret-token"
    assert b"Private label" in captured.content


@pytest.mark.anyio
async def test_gate_publisher_error_never_echoes_response_contact_data() -> None:
    private_value = "Private label 123456"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=private_value)

    publisher = GateSnapshotPublisher(
        base_url="http://test",
        bearer_token="secret-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GatePublishError) as error:
        await publisher.publish(
            account_alias="telegram_mark_ru",
            revision="revision-1",
            contacts=(ContactSnapshotItem("42", private_value),),
        )

    assert private_value not in str(error.value)


class FakeRequest:
    def __init__(self, **values: Any) -> None:
        self.values = values


@dataclass
class FakeTakeout:
    client: FakeTelethonClient

    async def __aenter__(self) -> FakeTakeout:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def __call__(self, request: object) -> list[SimpleNamespace]:
        self.client.takeout_requests.append(request)
        return [
            SimpleNamespace(
                phone="375290000001",
                first_name="Saved",
                last_name="Name",
                date=7,
            )
        ]


class FakeTelethonClient:
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.normal_requests: list[object] = []
        self.takeout_requests: list[object] = []
        self.takeout_options: dict[str, object] = {}

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> SimpleNamespace:
        return SimpleNamespace(id=1)

    async def __call__(self, request: object) -> SimpleNamespace:
        self.normal_requests.append(request)
        return SimpleNamespace(
            users=[SimpleNamespace(id=42, phone="375290000001", username="person")]
        )

    def takeout(self, **options: object) -> FakeTakeout:
        self.takeout_options = options
        return FakeTakeout(self)


@pytest.mark.anyio
async def test_telethon_adapter_only_uses_contact_reads() -> None:
    client = FakeTelethonClient()
    runtime = TelethonRuntime(
        client_type=object,
        string_session_type=object,
        get_contacts_request_type=FakeRequest,
        get_saved_request_type=FakeRequest,
        takeout_delay_error_type=RuntimeError,
    )

    result = await TelethonContactReader(client=client, runtime=runtime).read_contacts()

    assert client.connected is True
    assert client.disconnected is True
    assert len(client.normal_requests) == 1
    assert len(client.takeout_requests) == 1
    assert client.takeout_options == {
        "contacts": True,
        "users": False,
        "chats": False,
        "megagroups": False,
        "channels": False,
        "files": False,
    }
    assert result.saved_contacts[0].first_name == "Saved"
    assert result.telegram_users[0].telegram_user_id == "42"
