"""AQUB live Gate→runtime contract tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.application.aqub_live import AQUBLiveIngressAdapter
from app.application.aqub_s0_preflight import (
    AQUBDurableAcceptance,
    AQUBPreflightBinding,
    AQUBPreflightCommand,
    AQUBPreflightInbound,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
)

SIGNING_KEY = b"0123456789abcdef0123456789abcdef-live-test"
READY_AT = datetime(2026, 8, 7, 13, 17, tzinfo=UTC)


class Store:
    def __init__(self) -> None:
        self.commands: list[AQUBPreflightCommand] = []
        self._created: AQUBDurableAcceptance | None = None

    async def accept(self, command: AQUBPreflightCommand) -> AQUBDurableAcceptance:
        self.commands.append(command)
        if self._created is not None:
            return AQUBDurableAcceptance(
                status=AQUBReceiptStatus.COMMITTED,
                disposition=AQUBStoreDisposition.DUPLICATE,
                command_receipt_id=self._created.command_receipt_id,
                root_event_id=self._created.root_event_id,
                correlation_id=self._created.correlation_id,
                causation_id=self._created.causation_id,
            )
        self._created = AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id="raw-receipt-id",
            root_event_id="raw-root-id",
            correlation_id="raw-correlation-id",
            causation_id="raw-causation-id",
        )
        return self._created


class Runtime:
    def __init__(self) -> None:
        self.raw_bodies: list[bytes] = []
        self.signatures: list[str] = []

    async def post_envelope(
        self,
        *,
        raw_body: bytes,
        signature: str,
    ) -> dict[str, object]:
        self.raw_bodies.append(raw_body)
        self.signatures.append(signature)
        return {
            "ok": True,
            "result": {"kind": "status", "reply_text": "AQUB ok"},
        }


def binding() -> AQUBPreflightBinding:
    return AQUBPreflightBinding(
        enabled=True,
        bot_username="@AQubAgentBot",
        group_chat_id="-5052983997",
        ace_user_id="__unbound_ace__",
        alpha_user_id="7819816320",
        ready_at=READY_AT,
        receipt_signing_key=SIGNING_KEY,
    )


def inbound(
    *,
    text: str = "/status",
    update_id: int = 9001,
    actor_id: str = "7819816320",
    chat_id: str = "-5052983997",
) -> AQUBPreflightInbound:
    occurred = READY_AT + timedelta(seconds=1)
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 77,
            "date": int(occurred.timestamp()),
            "chat": {"id": int(chat_id), "type": "supergroup"},
            "from": {"id": int(actor_id), "is_bot": False},
            "text": text,
            "reply_to_message": {
                "message_id": 66,
                "from": {"id": 999, "is_bot": True},
                "text": (
                    "AQUB: пришлите программу ответом на это сообщение по шаблону "
                    "из минимум двух шагов (до 3500 символов)."
                ),
            },
        },
    }
    return AQUBPreflightInbound(
        bot_username="@AQubAgentBot",
        external_id=str(update_id),
        chat_id=chat_id,
        actor_id=actor_id,
        command=text,
        payload=payload,
        occurred_at=occurred,
        received_at=occurred + timedelta(seconds=1),
    )


@pytest.mark.anyio
async def test_live_forward_commits_gate_receipt_then_posts_only_opaque_refs() -> None:
    store = Store()
    runtime = Runtime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )

    result = await adapter.handle(inbound())

    assert result.accepted is True
    assert result.duplicate is False
    assert result.reason == "aqub_live_committed"
    assert len(store.commands) == 1
    assert len(runtime.raw_bodies) == 1

    raw = runtime.raw_bodies[0]
    expected_signature = hmac.new(SIGNING_KEY, raw, hashlib.sha256).hexdigest()
    assert runtime.signatures == [expected_signature]
    envelope = json.loads(raw)
    assert envelope["schema_version"] == "aqub.gate-envelope.v1"
    assert envelope["role"] == "alpha"
    assert envelope["chat_ref"].startswith("chat:")
    assert envelope["actor_ref"].startswith("actor:")
    assert envelope["message_id"].startswith("message:")
    assert envelope["reply_to_message_id"].startswith("message:")
    assert envelope["reply_context"] == "program_prompt"
    serialized = raw.decode()
    forbidden_values = (
        "-5052983997",
        "7819816320",
        '"message_id":77',
        '"message_id":66',
    )
    for forbidden in forbidden_values:
        assert forbidden not in serialized


@pytest.mark.anyio
async def test_exact_live_replay_reuses_gate_receipt_and_same_opaque_refs() -> None:
    store = Store()
    runtime = Runtime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )

    first = await adapter.handle(inbound())
    second = await adapter.handle(inbound())

    assert first.duplicate is False
    assert second.duplicate is True
    assert runtime.raw_bodies[0] == runtime.raw_bodies[1]


@pytest.mark.anyio
async def test_unbound_group_actor_is_forwarded_as_opaque_candidate() -> None:
    store = Store()
    runtime = Runtime()
    candidate_id = "123456789"
    value = inbound(text="/ace_join_123456", actor_id=candidate_id)

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    ).handle(value)

    assert result.accepted is True
    assert result.reason == "aqub_live_committed"
    assert len(store.commands) == 1
    assert len(runtime.raw_bodies) == 1
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["role"] == "candidate"
    assert envelope["actor_ref"].startswith("actor:")
    assert candidate_id not in runtime.raw_bodies[0].decode()


@pytest.mark.anyio
async def test_actor_outside_pinned_group_is_not_forwarded_or_stored() -> None:
    store = Store()
    runtime = Runtime()
    value = inbound(
        text="/ace_join_123456",
        actor_id="123456789",
        chat_id="-5052983998",
    )

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    ).handle(value)

    assert result.accepted is False
    assert result.reason == "aqub_route_not_owned"
    assert store.commands == []
    assert runtime.raw_bodies == []