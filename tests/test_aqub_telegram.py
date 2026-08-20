"""Integration coverage for the permanent AQUB Telegram production ingress."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.api.aqub_telegram import (
    _live_response,
    get_aqub_production_binding,
    get_aqub_transport_store,
    router,
)
from app.api.telegram_ingress import TELEGRAM_WEBHOOK_CHAIN
from app.application.aqub_live import AQUBLiveForwardResult
from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUBDurableAcceptance,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
    AQUBTransportBinding,
    AQUBTransportCommand,
    AQUBTransportContentKind,
)
from app.core.config import Settings, get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

TEST_CHAT_ID = "-1001234567890"
TEST_ALPHA_USER_ID = "123456789"
TEST_READY_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
TEST_SIGNING_KEY = b"0123456789abcdef0123456789abcdef-test"
_MISSING = object()


class DurableStore:
    def __init__(self) -> None:
        self.commands: list[AQUBTransportCommand] = []
        self.acceptances: dict[str, AQUBDurableAcceptance] = {}

    async def accept(self, command: AQUBTransportCommand) -> AQUBDurableAcceptance:
        self.commands.append(command)
        key = command.external_id
        existing = self.acceptances.get(key)
        if existing is not None:
            return AQUBDurableAcceptance(
                status=AQUBReceiptStatus.COMMITTED,
                disposition=AQUBStoreDisposition.DUPLICATE,
                command_receipt_id=existing.command_receipt_id,
                root_event_id=existing.root_event_id,
                correlation_id=existing.correlation_id,
                causation_id=existing.causation_id,
            )
        created = AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id="raw-receipt SECRET-RECEIPT",
            root_event_id="raw-root SECRET-ROOT",
            correlation_id="raw-correlation SECRET-CORRELATION",
            causation_id="raw-causation SECRET-CAUSATION",
        )
        self.acceptances[key] = created
        return created


class FailingStore(DurableStore):
    async def accept(self, command: AQUBTransportCommand) -> AQUBDurableAcceptance:
        self.commands.append(command)
        raise RuntimeError("synthetic_store_unavailable")


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "TELEGRAM_AQUB_WEBHOOK_SECRET": "webhook SECRET-WEBHOOK",
        "GATE_AQUB_RECEIPT_SIGNING_KEY": (
            "0123456789abcdef0123456789abcdef SECRET-SIGNING"
        ),
    }
    values.update(changes)
    return Settings(**values)


def _binding() -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id=TEST_CHAT_ID,
        ace_user_id="__unbound_ace__",
        alpha_user_id=TEST_ALPHA_USER_ID,
        ready_at=TEST_READY_AT,
        receipt_signing_key=TEST_SIGNING_KEY,
    )


def _update(**message_changes: object) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": 501,
        "date": int((TEST_READY_AT + timedelta(seconds=1)).timestamp()),
        "chat": {"id": int(TEST_CHAT_ID), "type": "supergroup"},
        "from": {"id": int(TEST_ALPHA_USER_ID), "is_bot": False},
        "text": "/aqub_ping",
    }
    message.update(message_changes)
    return {"update_id": 500, "message": message}


def _photo_update(
    *,
    caption: object = _MISSING,
    photo: object = _MISSING,
    **message_changes: object,
) -> dict[str, object]:
    update = _update()
    message = update["message"]
    assert isinstance(message, dict)
    message.pop("text")
    message["photo"] = (
        [
            {
                "file_id": "synthetic-file-small",
                "file_unique_id": "synthetic-unique-small",
                "width": 320,
                "height": 240,
                "file_size": 1_234,
            },
            {
                "file_id": "synthetic-file-large",
                "file_unique_id": "synthetic-unique-large",
                "width": 1280,
                "height": 960,
                "file_size": 12_345,
            },
        ]
        if photo is _MISSING
        else photo
    )
    if caption is not _MISSING:
        message["caption"] = caption
    message.update(message_changes)
    return update


def _client(
    settings: Settings,
    store: DurableStore,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> TestClient:
    app = FastAPI()
    if http_client is not None:
        app.state.http_client = http_client
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_aqub_transport_store] = lambda: store
    app.dependency_overrides[get_aqub_production_binding] = _binding
    return TestClient(app)


def _post(
    client: TestClient,
    update: dict[str, object],
    secret: str = "webhook SECRET-WEBHOOK",
):
    return client.post(
        "/telegram/aqub/preflight",
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        json=update,
    )


def _receipt_fields(text: str) -> dict[str, str]:
    receipt_line = next(
        line for line in text.splitlines() if line.startswith("AQUB_RECEIPT ")
    )
    return {
        key: value
        for item in receipt_line.removeprefix("AQUB_RECEIPT ").split()
        for key, value in [item.split("=", 1)]
    }


def test_permanent_ping_route_owns_once_and_duplicate_reuses_receipt() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        first = _post(client, _update())
        second = _post(client, _update())

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["method"] == "sendMessage"
    assert second_body["method"] == "sendMessage"
    assert first_body["chat_id"] == int(TEST_CHAT_ID)

    first_receipt = _receipt_fields(first_body["text"])
    second_receipt = _receipt_fields(second_body["text"])
    assert first_receipt["status"] == "committed"
    assert first_receipt["role"] == "alpha"
    assert first_receipt["duplicate"] == "false"
    assert second_receipt["duplicate"] == "true"
    assert second_receipt["receipt_ref"] == first_receipt["receipt_ref"]
    assert second_receipt["root_ref"] == first_receipt["root_ref"]
    assert [command.external_id for command in store.commands] == ["500", "500"]


def test_aqub_route_does_not_enter_existing_dispatch_chain() -> None:
    assert all("aqub" not in route for route in TELEGRAM_WEBHOOK_CHAIN)


def test_unbound_group_touches_no_storage() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _update(chat={"id": -1007999, "type": "supergroup"}),
        )

    assert response.status_code == 200
    assert response.json()["owned"] is False
    assert store.commands == []


def test_wrong_webhook_secret_is_forbidden() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update(), secret="wrong")

    assert response.status_code == 403
    assert store.commands == []


def test_ordinary_owned_message_without_runtime_url_is_durable_and_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update(text="обычное сообщение"))

    assert response.status_code == 200
    assert response.json() == {
        "method": "sendMessage",
        "chat_id": int(TEST_CHAT_ID),
        "text": "Записал.",
    }
    assert [command.command for command in store.commands] == ["обычное сообщение"]


def test_duplicate_plain_text_reuses_one_durable_acceptance_and_same_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    update = _update(text="Съел омлет")
    with _client(_settings(), store) as client:
        first = _post(client, update)
        second = _post(client, update)

    expected = {
        "method": "sendMessage",
        "chat_id": int(TEST_CHAT_ID),
        "text": "Записал.",
    }
    assert first.json() == second.json() == expected
    assert len(store.acceptances) == 1
    assert [command.external_id for command in store.commands] == ["500", "500"]


def test_runtime_exception_after_commit_still_confirms_plain_alpha_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_AQUB_RUNTIME_URL", "https://aqub.example")
    store = DurableStore()

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic unavailable", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    try:
        with _client(_settings(), store, http_client=http_client) as client:
            response = _post(client, _update(text="Выпил воду"))
    finally:
        asyncio.run(http_client.aclose())

    assert response.status_code == 200
    assert response.json()["text"] == "Записал."
    assert [command.command for command in store.commands] == ["Выпил воду"]


def test_command_without_runtime_url_remains_retryable_and_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update(text="/status"))

    assert response.status_code == 503
    assert response.json()["detail"] == "aqub_runtime_unavailable"
    assert [command.command for command in store.commands] == ["/status"]


def test_unbound_actor_plain_text_gets_no_storage_or_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _update(
                text="Съел омлет",
                **{"from": {"id": 987654321, "is_bot": False}},
            ),
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


def test_owned_photo_reference_is_durable_and_confirmed_without_runtime_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_AQUB_RUNTIME_URL", "https://aqub.example")
    store = DurableStore()
    update = _photo_update(caption="Съел омлет")
    with _client(_settings(), store) as client:
        response = _post(client, update)

    assert response.status_code == 200
    assert response.json() == {
        "method": "sendMessage",
        "chat_id": int(TEST_CHAT_ID),
        "text": "Фото принято.",
    }
    assert len(store.commands) == 1
    command = store.commands[0]
    assert command.content_kind is AQUBTransportContentKind.PHOTO
    assert command.command is None
    assert command.photo is not None
    assert command.photo.file_id == "synthetic-file-large"
    assert command.photo.file_unique_id == "synthetic-unique-large"
    assert command.payload == update


def test_owned_photo_without_caption_is_accepted() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update())

    assert response.status_code == 200
    assert response.json()["text"] == "Фото принято."
    assert len(store.commands) == 1


def test_slash_like_photo_caption_remains_raw_metadata_only() -> None:
    store = DurableStore()
    update = _photo_update(caption="/status")
    with _client(_settings(), store) as client:
        response = _post(client, update)

    assert response.status_code == 200
    assert response.json()["text"] == "Фото принято."
    assert store.commands[0].command is None
    assert store.commands[0].payload == update


def test_duplicate_photo_reuses_one_acceptance_and_same_confirmation() -> None:
    store = DurableStore()
    update = _photo_update(caption="Ужин")
    with _client(_settings(), store) as client:
        first = _post(client, update)
        second = _post(client, update)

    assert first.json() == second.json()
    assert first.json()["text"] == "Фото принято."
    assert len(store.acceptances) == 1
    assert [command.external_id for command in store.commands] == ["500", "500"]


@pytest.mark.parametrize(
    ("photo", "caption"),
    [
        ([], _MISSING),
        ([{"file_unique_id": "u", "width": 320, "height": 240}], _MISSING),
        (
            [
                {
                    "file_id": " ",
                    "file_unique_id": "u",
                    "width": 320,
                    "height": 240,
                }
            ],
            _MISSING,
        ),
        (
            [
                {
                    "file_id": "f",
                    "file_unique_id": "u",
                    "width": True,
                    "height": 240,
                }
            ],
            _MISSING,
        ),
        (
            [
                {
                    "file_id": "f",
                    "file_unique_id": "u",
                    "width": 320,
                    "height": 240,
                    "file_size": None,
                }
            ],
            _MISSING,
        ),
        (
            [
                {
                    "file_id": "f",
                    "file_unique_id": "u",
                    "width": 0,
                    "height": 240,
                    "file_size": -1,
                }
            ],
            _MISSING,
        ),
        (
            [
                {
                    "file_id": "f",
                    "file_unique_id": "u",
                    "width": 320,
                    "height": 240,
                }
            ],
            42,
        ),
    ],
)
def test_malformed_photo_gets_no_storage_or_confirmation(
    photo: object,
    caption: object,
) -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update(photo=photo, caption=caption))

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


@pytest.mark.parametrize(
    "conflict",
    [
        {"text": "ambiguous"},
        {"document": {"file_id": "doc", "file_unique_id": "doc-u"}},
        {"forward_origin": {"type": "user"}},
        {"reply_to_message": {"message_id": 1}},
        {"external_reply": {"origin": {"type": "user"}}},
        {"sender_chat": {"id": -10099, "type": "supergroup"}},
        {"media_group_id": "synthetic-album"},
        {"reply_to_story": {"chat": {"id": -10099}, "id": 7}},
        {
            "guest_bot_caller_user": {
                "id": 987654321,
                "is_bot": False,
                "first_name": "Third party",
            }
        },
        {
            "guest_bot_caller_chat": {
                "id": -10099,
                "type": "supergroup",
                "title": "Third party",
            }
        },
        {
            "sender_business_bot": {
                "id": 987654321,
                "is_bot": True,
                "first_name": "Third party bot",
            }
        },
        {
            "receiver_user": {
                "id": 987654321,
                "is_bot": False,
                "first_name": "Third party",
            }
        },
        {
            "caption_entities": [
                {
                    "type": "text_mention",
                    "offset": 0,
                    "length": 4,
                    "user": {
                        "id": 987654321,
                        "is_bot": False,
                        "first_name": "Third party",
                    },
                }
            ]
        },
        {"synthetic_unknown_message_field": True},
    ],
)
def test_ambiguous_or_third_party_photo_context_is_rejected(
    conflict: dict[str, object],
) -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update(**conflict))

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


def test_unknown_photo_size_field_is_rejected_before_storage() -> None:
    store = DurableStore()
    photo = [
        {
            "file_id": "synthetic-file",
            "file_unique_id": "synthetic-unique",
            "width": 320,
            "height": 240,
            "synthetic_unknown_photo_field": {"user_id": 987654321},
        }
    ]
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update(photo=photo))

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


@pytest.mark.parametrize(
    "changes",
    [
        {"message_id": True},
        {"date": True},
        {"caption": "x" * 1_025},
    ],
)
def test_photo_exact_integer_and_caption_bounds_are_enforced(
    changes: dict[str, object],
) -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update(**changes))

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert store.commands == []


def test_photo_from_unbound_actor_gets_no_storage_or_confirmation() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _photo_update(**{"from": {"id": 987654321, "is_bot": False}}),
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


def test_photo_from_other_group_gets_no_storage_or_confirmation() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _photo_update(chat={"id": -1007999, "type": "supergroup"}),
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


def test_photo_store_failure_yields_no_confirmation() -> None:
    store = FailingStore()
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update())

    assert response.status_code == 503
    assert "method" not in response.json()
    assert len(store.commands) == 1


def test_pre_ready_photo_gets_no_storage_or_confirmation() -> None:
    store = DurableStore()
    before_ready = int((TEST_READY_AT - timedelta(seconds=1)).timestamp())
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update(date=before_ready))

    assert response.status_code == 503
    assert response.json()["detail"] == "aqub_event_before_ready"
    assert "method" not in response.json()
    assert store.commands == []


def test_future_photo_gets_no_storage_or_confirmation() -> None:
    store = DurableStore()
    future = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    with _client(_settings(), store) as client:
        response = _post(client, _photo_update(date=future))

    assert response.status_code == 503
    assert response.json()["detail"] == "aqub_timestamp_order_invalid"
    assert "method" not in response.json()
    assert store.commands == []


def test_bot_authored_photo_gets_no_storage_or_confirmation() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _photo_update(
                **{"from": {"id": int(TEST_ALPHA_USER_ID), "is_bot": True}}
            ),
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


def test_boolean_update_id_photo_gets_no_storage_or_confirmation() -> None:
    store = DurableStore()
    update = _photo_update()
    update["update_id"] = True
    with _client(_settings(), store) as client:
        response = _post(client, update)

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert "method" not in response.json()
    assert store.commands == []


def test_downstream_reply_precedes_plain_capture_confirmation() -> None:
    response = _live_response(
        AQUBLiveForwardResult(
            accepted=True,
            duplicate=False,
            reason="aqub_live_committed",
            downstream={"result": {"reply_text": "AQUB ответил"}},
            captured_content=AQUBTransportContentKind.TEXT,
        ),
        chat_id=TEST_CHAT_ID,
    )

    assert response["text"] == "AQUB ответил"


def test_visible_receipt_reply_redacts_raw_identifiers_and_secrets() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update())

    reply_text = response.json()["text"]
    assert "AQUB_RECEIPT " in reply_text
    for forbidden in (
        "SECRET-RECEIPT",
        "SECRET-WEBHOOK",
        "SECRET-SIGNING",
        TEST_CHAT_ID,
        TEST_ALPHA_USER_ID,
        "/aqub_ping",
    ):
        assert forbidden not in reply_text
