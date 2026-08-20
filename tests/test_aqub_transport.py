from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUBDiagnosticPingAdapter,
    AQUBDurableAcceptance,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
    AQUBTransportBinding,
    AQUBTransportInbound,
    AQUBTransportRole,
)

READY_AT = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
SIGNING_KEY = b"transport-repository-test-key-32bytes!!"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeStore:
    def __init__(
        self,
        *,
        disposition: AQUBStoreDisposition = AQUBStoreDisposition.CREATED,
        status: AQUBReceiptStatus = AQUBReceiptStatus.COMMITTED,
    ) -> None:
        self.disposition = disposition
        self.status = status
        self.commands = []

    async def accept(self, command):
        self.commands.append(command)
        return AQUBDurableAcceptance(
            status=self.status,
            disposition=self.disposition,
            command_receipt_id="raw-receipt-id SECRET-RECEIPT",
            root_event_id="raw-root-event SECRET-ROOT",
            correlation_id="raw-correlation SECRET-CORRELATION",
            causation_id="raw-causation SECRET-CAUSATION",
        )


def _binding(**overrides) -> AQUBTransportBinding:
    values = {
        "enabled": True,
        "bot_username": AQUB_BOT_USERNAME,
        "group_chat_id": "-100-secret-group",
        "ace_user_id": "111-secret-ace",
        "alpha_user_id": "222-secret-alpha",
        "ready_at": READY_AT,
        "receipt_signing_key": SIGNING_KEY,
    }
    values.update(overrides)
    return AQUBTransportBinding(**values)


def _inbound(**overrides) -> AQUBTransportInbound:
    values = {
        "bot_username": AQUB_BOT_USERNAME,
        "external_id": "telegram-update-9001",
        "chat_id": "-100-secret-group",
        "actor_id": "222-secret-alpha",
        "command": "/aqub_ping",
        "payload": {
            "update_id": 9001,
            "message": {"text": "/aqub_ping", "chat": {"id": -1009001}},
        },
        "occurred_at": READY_AT,
        "received_at": READY_AT + timedelta(seconds=1),
    }
    values.update(overrides)
    return AQUBTransportInbound(**values)


@pytest.mark.anyio
async def test_disabled_binding_owns_nothing() -> None:
    store = FakeStore()
    result = await AQUBDiagnosticPingAdapter(
        binding=AQUBTransportBinding(),
        store=store,
    ).handle(_inbound())

    assert result.owned is False
    assert result.accepted is False
    assert store.commands == []


@pytest.mark.anyio
async def test_exact_alpha_ping_is_committed_signed_and_exclusive() -> None:
    store = FakeStore()
    binding = _binding()
    result = await AQUBDiagnosticPingAdapter(binding=binding, store=store).handle(
        _inbound()
    )

    assert result.owned is True
    assert result.accepted is True
    assert result.reply_text == "AQUB: связь alpha подтверждена."
    assert len(store.commands) == 1
    assert store.commands[0].role is AQUBTransportRole.ALPHA
    assert store.commands[0].occurred_at >= READY_AT

    receipt = result.receipt
    assert receipt is not None
    assert receipt.status == "committed"
    assert receipt.delivery_status == "not_run"
    assert receipt.verify(SIGNING_KEY)
    assert receipt.owned_path == "/telegram/aqub/preflight"


@pytest.mark.anyio
async def test_exact_ace_binding_gets_ace_role() -> None:
    store = FakeStore()
    result = await AQUBDiagnosticPingAdapter(
        binding=_binding(), store=store
    ).handle(_inbound(actor_id="111-secret-ace"))

    assert result.accepted is True
    assert store.commands[0].role is AQUBTransportRole.ACE
    assert result.receipt is not None
    assert result.receipt.role == "ace"


@pytest.mark.anyio
async def test_receipt_contains_no_raw_ids_text_or_signing_key() -> None:
    store = FakeStore()
    result = await AQUBDiagnosticPingAdapter(
        binding=_binding(), store=store
    ).handle(_inbound())

    receipt = result.receipt
    assert receipt is not None
    serialized = json.dumps(
        {**receipt.unsigned_dict(), "signature": receipt.signature},
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        "-100-secret-group",
        "222-secret-alpha",
        "telegram-update-9001",
        "/aqub_ping",
        "must-not-leak",
        "SECRET-RECEIPT",
        "SECRET-ROOT",
        "SECRET-CORRELATION",
        "SECRET-CAUSATION",
        SIGNING_KEY.decode(),
    ):
        assert forbidden not in serialized


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("change", "expected_owned"),
    [
        ({"bot_username": "@WrongBot"}, False),
        ({"actor_id": "333-other"}, False),
        ({"command": "/other"}, True),
        ({"occurred_at": READY_AT - timedelta(microseconds=1)}, True),
    ],
)
async def test_only_exact_bot_and_actor_can_be_owned(change, expected_owned) -> None:
    store = FakeStore()
    result = await AQUBDiagnosticPingAdapter(
        binding=_binding(), store=store
    ).handle(_inbound(**change))

    assert result.owned is expected_owned
    assert result.accepted is False
    assert store.commands == []


@pytest.mark.anyio
async def test_exact_owner_ping_in_another_group_is_owned_and_accepted() -> None:
    store = FakeStore()
    result = await AQUBDiagnosticPingAdapter(
        binding=_binding(), store=store
    ).handle(_inbound(chat_id="-100-other"))

    assert result.owned is True
    assert result.accepted is True
    assert [command.chat_id for command in store.commands] == ["-100-other"]


@pytest.mark.anyio
async def test_duplicate_reuses_store_result_without_claiming_delivery() -> None:
    store = FakeStore(disposition=AQUBStoreDisposition.DUPLICATE)
    result = await AQUBDiagnosticPingAdapter(
        binding=_binding(), store=store
    ).handle(_inbound())

    assert result.accepted is True
    assert result.receipt is not None
    assert result.receipt.duplicate is True
    assert result.receipt.result_json == {
        "disposition": "duplicate",
        "command": "aqub_ping",
    }
    assert result.receipt.delivery_status == "not_run"


@pytest.mark.anyio
async def test_uncommitted_store_result_cannot_create_receipt_or_reply() -> None:
    with pytest.raises(RuntimeError, match="aqub_store_status_invalid"):
        await AQUBDiagnosticPingAdapter(
            binding=_binding(),
            store=FakeStore(status="pending"),  # type: ignore[arg-type]
        ).handle(_inbound())


@pytest.mark.anyio
async def test_arbitrary_store_disposition_cannot_enter_result_json() -> None:
    with pytest.raises(RuntimeError, match="aqub_store_disposition_invalid"):
        await AQUBDiagnosticPingAdapter(
            binding=_binding(),
            store=FakeStore(
                disposition="SECRET-ARBITRARY-RESULT"  # type: ignore[arg-type]
            ),
        ).handle(_inbound())


def test_enabled_binding_requires_exact_identity_and_safe_key() -> None:
    with pytest.raises(ValueError, match="aqub_bot_identity_mismatch"):
        _binding(bot_username="AQubAgentBot")
    with pytest.raises(ValueError, match="aqub_receipt_signing_key_too_short"):
        _binding(receipt_signing_key=b"short")
