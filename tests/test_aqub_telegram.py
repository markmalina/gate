"""Integration coverage for the permanent AQUB Telegram production ingress."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import app.api.aqub_telegram as aqub_telegram_api
import httpx
import pytest
from app.api.aqub_telegram import (
    _deliver_live_messages,
    _live_response,
    _project_inbound,
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
from app.infrastructure.aqub_live import ParsedAQUBDelivery
from fastapi import FastAPI, Request
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
        "GATE_AQUB_RECEIPT_SIGNING_KEY": ("0123456789abcdef0123456789abcdef SECRET-SIGNING"),
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


def _document_update() -> dict[str, object]:
    update = _update()
    message = update["message"]
    assert isinstance(message, dict)
    message.pop("text")
    message["caption"] = "Разбери договор"
    message["document"] = {
        "file_id": "synthetic-document",
        "file_unique_id": "synthetic-document-unique",
        "file_name": "contract.pdf",
        "mime_type": "application/pdf",
        "file_size": 12_345,
    }
    return update


def test_general_document_projects_as_bounded_document_reference() -> None:
    projected = _project_inbound(
        _document_update(),
        bot_username=AQUB_BOT_USERNAME,
        received_at=TEST_READY_AT + timedelta(seconds=2),
    )
    assert projected is not None
    assert projected.content_kind is AQUBTransportContentKind.DOCUMENT
    assert projected.document is not None
    assert projected.document.file_name == "contract.pdf"
    assert projected.command is None


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
    receipt_line = next(line for line in text.splitlines() if line.startswith("AQUB_RECEIPT "))
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


def test_same_bound_actor_ping_is_accepted_in_another_group() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _update(chat={"id": -1007999, "type": "supergroup"}),
        )

    assert response.status_code == 200
    assert response.json()["method"] == "sendMessage"
    assert response.json()["chat_id"] == -1007999
    assert [command.chat_id for command in store.commands] == ["-1007999"]


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
        "text": "Сообщение сохранил, но сейчас не смог разобрать питание. Попробуйте ещё раз.",
    }
    assert [command.command for command in store.commands] == ["обычное сообщение"]


def test_same_bound_actor_text_is_captured_in_another_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _update(
                text="Съел омлет",
                chat={"id": -1007999, "type": "supergroup"},
            ),
        )

    assert response.status_code == 200
    assert response.json() == {
        "method": "sendMessage",
        "chat_id": -1007999,
        "text": "Сообщение сохранил, но сейчас не смог разобрать питание. Попробуйте ещё раз.",
    }
    assert [command.chat_id for command in store.commands] == ["-1007999"]
    assert [command.command for command in store.commands] == ["Съел омлет"]


def test_duplicate_plain_text_retries_processing_without_second_fallback_reply(
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
        "text": "Сообщение сохранил, но сейчас не смог разобрать питание. Попробуйте ещё раз.",
    }
    assert first.json() == expected
    assert second.json() == {
        "ok": True,
        "owned": True,
        "accepted": True,
        "reason": "aqub_live_durable_only",
        "duplicate": True,
        "runtime_error_code": "aqub_runtime_unconfigured",
    }
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
    assert response.json()["text"] == (
        "Сообщение сохранил, но сейчас не смог разобрать питание. Попробуйте ещё раз."
    )
    assert [command.command for command in store.commands] == ["Выпил воду"]


def test_command_without_runtime_url_remains_retryable_and_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATE_AQUB_RUNTIME_URL", raising=False)
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(client, _update(text="/status"))

    assert response.status_code == 503
    assert response.json()["detail"] == "aqub_runtime_unconfigured"
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


def test_duplicate_photo_retries_processing_without_second_fallback_reply() -> None:
    store = DurableStore()
    update = _photo_update(caption="Ужин")
    with _client(_settings(), store) as client:
        first = _post(client, update)
        second = _post(client, update)

    assert first.json()["text"] == "Фото принято."
    assert second.json()["reason"] == "aqub_live_durable_only"
    assert "method" not in second.json()
    assert len(store.acceptances) == 1
    assert [command.external_id for command in store.commands] == ["500", "500"]


def test_album_photo_projects_strict_media_group_without_losing_caption() -> None:
    projected = _project_inbound(
        _photo_update(caption="Курица", media_group_id="synthetic-album"),
        bot_username=AQUB_BOT_USERNAME,
        received_at=TEST_READY_AT + timedelta(seconds=2),
    )
    assert projected is not None
    assert projected.content_kind is AQUBTransportContentKind.PHOTO
    assert projected.media_group_id == "synthetic-album"
    assert projected.payload["message"]["caption"] == "Курица"


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


def test_photo_from_same_bound_actor_in_other_group_is_durable_and_confirmed() -> None:
    store = DurableStore()
    with _client(_settings(), store) as client:
        response = _post(
            client,
            _photo_update(chat={"id": -1007999, "type": "supergroup"}),
        )

    assert response.status_code == 200
    assert response.json() == {
        "method": "sendMessage",
        "chat_id": -1007999,
        "text": "Фото принято.",
    }
    assert [command.chat_id for command in store.commands] == ["-1007999"]
    assert [command.content_kind for command in store.commands] == [AQUBTransportContentKind.PHOTO]


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

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "owned": False,
        "accepted": False,
        "reason": "aqub_event_before_ready",
    }
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
            _photo_update(**{"from": {"id": int(TEST_ALPHA_USER_ID), "is_bot": True}}),
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


def test_general_runtime_failure_uses_neutral_fallback() -> None:
    response = _live_response(
        AQUBLiveForwardResult(
            accepted=True,
            duplicate=False,
            reason="aqub_live_durable_only",
            captured_content=AQUBTransportContentKind.TEXT,
        ),
        chat_id=TEST_CHAT_ID,
        general_route=True,
    )

    assert response["text"] == "Сейчас не смог ответить. Попробуйте ещё раз."
    assert "питан" not in response["text"].lower()


def test_duplicate_durable_only_response_carries_sanitized_runtime_code() -> None:
    response = _live_response(
        AQUBLiveForwardResult(
            accepted=True,
            duplicate=True,
            reason="aqub_live_durable_only",
            captured_content=AQUBTransportContentKind.TEXT,
            runtime_error_code="aqub_runtime_binding_not_found",
        ),
        chat_id=TEST_CHAT_ID,
        general_route=True,
    )

    assert response == {
        "ok": True,
        "owned": True,
        "accepted": True,
        "reason": "aqub_live_durable_only",
        "duplicate": True,
        "runtime_error_code": "aqub_runtime_binding_not_found",
    }


def test_internal_runtime_probe_precedes_telegram_fallback() -> None:
    response = _live_response(
        AQUBLiveForwardResult(
            accepted=True,
            duplicate=False,
            reason="aqub_live_durable_only",
            captured_content=AQUBTransportContentKind.TEXT,
            runtime_error_code="aqub_runtime_unconfigured",
        ),
        chat_id=TEST_CHAT_ID,
        general_route=True,
        runtime_diagnostic=True,
    )

    assert response == {
        "ok": True,
        "owned": True,
        "accepted": True,
        "reason": "aqub_live_durable_only",
        "duplicate": False,
        "runtime_error_code": "aqub_runtime_unconfigured",
    }
    assert "method" not in response


def test_internal_runtime_probe_exposes_allowlisted_no_reply_kind() -> None:
    response = _live_response(
        AQUBLiveForwardResult(
            accepted=True,
            duplicate=True,
            reason="aqub_live_committed",
            downstream={
                "result": {
                    "kind": "ignored",
                    "reply_text": None,
                    "suppress_reply": True,
                }
            },
            captured_content=AQUBTransportContentKind.TEXT,
        ),
        chat_id=TEST_CHAT_ID,
        general_route=True,
        runtime_diagnostic=True,
    )

    assert response == {
        "ok": True,
        "owned": True,
        "accepted": True,
        "reason": "aqub_live_committed",
        "duplicate": True,
        "runtime_downstream_kind": "ignored",
    }
    assert "method" not in response


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


@pytest.mark.anyio
async def test_semantic_messages_use_stable_durable_delivery_keys_and_terminal_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ParsedAQUBDelivery] = []

    class SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    class SessionFactory:
        def __call__(self) -> SessionContext:
            return SessionContext()

    class Runtime:
        session_factory = SessionFactory()

    class DeliveryService:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["raw_chat_id"] == TEST_CHAT_ID

        async def deliver(self, command: ParsedAQUBDelivery) -> dict[str, object]:
            captured.append(command)
            return {
                "ack": {"status": "committed"},
                "terminal": {"status": "sent"},
            }

    monkeypatch.setattr(aqub_telegram_api, "get_database_runtime", lambda request: Runtime())
    monkeypatch.setattr(aqub_telegram_api, "AQUBLiveDeliveryService", DeliveryService)
    app = FastAPI()
    async with httpx.AsyncClient(trust_env=False) as http_client:
        app.state.http_client = http_client
        request = Request({"type": "http", "app": app, "headers": []})
        live = AQUBLiveForwardResult(
            accepted=True,
            duplicate=False,
            reason="aqub_live_committed",
            downstream={
                "result": {
                    "reply_text": "Состав\n\nРасчёт\n\nПроверка\n\nСколько граммов?",
                    "force_reply": True,
                }
            },
            delivery_ref="receipt:opaque-synthetic",
            reply_to_message_id=501,
        )
        settings = _settings(TELEGRAM_AQUB_BOT_TOKEN="synthetic-token")

        assert await _deliver_live_messages(
            request=request,
            settings=settings,
            live=live,
            chat_id=TEST_CHAT_ID,
        )
        assert await _deliver_live_messages(
            request=request,
            settings=settings,
            live=live,
            chat_id=TEST_CHAT_ID,
        )

    assert [item.text for item in captured[:4]] == [
        "Состав",
        "Расчёт",
        "Проверка",
        "Сколько граммов?",
    ]
    assert [item.idempotency_key for item in captured[:4]] == [
        item.idempotency_key for item in captured[4:]
    ]
    assert [item.command_id for item in captured[:4]] == [item.command_id for item in captured[4:]]
    assert [item.request_sha256 for item in captured[:4]] == [
        item.request_sha256 for item in captured[4:]
    ]
    assert [item.force_reply for item in captured[:4]] == [False, False, False, True]
    assert [item.reply_to_message_id for item in captured] == [501] * 8


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
