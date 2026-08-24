"""Focused exact-route proofs for the Kakhovich General Cube Gate seam."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.aqub_live import AQUBLiveIngressAdapter
from app.application.aqub_transport import (
    AQUBTransportBinding,
    AQUBTransportContentKind,
    AQUBTransportInbound,
)
from app.infrastructure.aqub_general_route import general_route_evidence_from_receipt
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.models.domain import TelegramReceipt, TelegramReceiptStatus

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
SIGNING_KEY = b"general-route-test-signing-key-32-bytes"


class _Store:
    def __init__(self) -> None:
        self.calls = 0

    async def accept(self, command):
        from app.application.aqub_transport import (
            AQUBDurableAcceptance,
            AQUBReceiptStatus,
            AQUBStoreDisposition,
        )

        self.calls += 1
        return AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id=str(uuid4()),
            root_event_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id="synthetic:cause",
        )


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    async def post_envelope(self, *, raw_body: bytes, signature: str):
        self.calls += 1
        return {"ok": True, "result": {"kind": "general", "reply_text": "ok"}}


def _binding(*, group: str, actor: str) -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username="@AQubAgentBot",
        group_chat_id=group,
        ace_user_id="__unbound_ace__",
        alpha_user_id=actor,
        ready_at=NOW,
        receipt_signing_key=SIGNING_KEY,
    )


def _inbound(*, group: str, actor: str) -> AQUBTransportInbound:
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id="41",
        chat_id=group,
        actor_id=actor,
        command="Свободный вопрос",
        payload={
            "update_id": 41,
            "message": {
                "message_id": 42,
                "date": int(NOW.timestamp()),
                "chat": {"id": -43, "type": "supergroup"},
                "from": {"id": 44, "is_bot": False},
                "text": "Свободный вопрос",
            },
        },
        occurred_at=NOW,
        received_at=NOW,
        content_kind=AQUBTransportContentKind.TEXT,
    )


@pytest.mark.anyio
async def test_owner_is_rejected_outside_exact_bound_group() -> None:
    store, runtime = _Store(), _Runtime()
    result = await AQUBLiveIngressAdapter(
        binding=_binding(group="group:food", actor="actor:owner"),
        store=store,
        runtime=runtime,
    ).handle(_inbound(group="group:other", actor="actor:owner"))
    assert result.accepted is False
    assert result.reason == "aqub_route_not_owned"
    assert store.calls == runtime.calls == 0


@pytest.mark.anyio
async def test_exact_group_and_actor_forward_once() -> None:
    store, runtime = _Store(), _Runtime()
    result = await AQUBLiveIngressAdapter(
        binding=_binding(group="group:general", actor="actor:general"),
        store=store,
        runtime=runtime,
    ).handle(_inbound(group="group:general", actor="actor:general"))
    assert result.accepted is True
    assert result.downstream == {
        "ok": True,
        "result": {"kind": "general", "reply_text": "ok"},
    }
    assert store.calls == runtime.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("group", "actor"),
    (("group:other", "actor:general"), ("group:general", "actor:other")),
)
async def test_general_route_denies_wrong_group_or_actor(group: str, actor: str) -> None:
    store, runtime = _Store(), _Runtime()
    result = await AQUBLiveIngressAdapter(
        binding=_binding(group="group:general", actor="actor:general"),
        store=store,
        runtime=runtime,
    ).handle(_inbound(group=group, actor=actor))
    assert result.accepted is False
    assert store.calls == runtime.calls == 0


def test_terminal_receipt_projects_private_ids_without_repr_exposure() -> None:
    receipt = TelegramReceipt(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        update_id=45,
        payload={
            "message": {
                "chat": {"id": -46, "type": "supergroup"},
                "from": {"id": 47, "is_bot": False},
            }
        },
        status=TelegramReceiptStatus.PROCESSED.value,
        received_at=NOW,
        processed_at=NOW,
    )
    evidence = general_route_evidence_from_receipt(receipt)
    assert evidence is not None
    assert "46" not in repr(evidence)
    assert "47" not in repr(evidence)
