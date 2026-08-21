from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.api.aqub_telegram import _project_inbound
from app.application.aqub_live import AQUBLiveIngressAdapter
from app.application.aqub_transport import (
    AQUBDurableAcceptance,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
    AQUBTransportBinding,
    AQUBTransportContentKind,
)


def _voice_update() -> dict[str, object]:
    return {
        "update_id": 991,
        "message": {
            "message_id": 77,
            "from": {"id": 1001, "is_bot": False},
            "date": 1_786_000_000,
            "chat": {"id": -10042, "type": "supergroup"},
            "voice": {
                "file_id": "provider-secret-file-id",
                "file_unique_id": "stable-voice-id",
                "duration": 12,
                "mime_type": "audio/ogg",
                "file_size": 12345,
            },
        },
    }


class _Store:
    async def accept(self, _command: object) -> AQUBDurableAcceptance:
        return AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id="receipt-1",
            root_event_id="root-1",
            correlation_id="correlation-1",
            causation_id="causation-1",
        )


class _Runtime:
    def __init__(self) -> None:
        self.raw_body = b""

    async def post_envelope(self, *, raw_body: bytes, signature: str) -> dict[str, object]:
        assert len(signature) == 64
        self.raw_body = raw_body
        return {
            "ok": True,
            "result": {
                "kind": "food_needs_clarification",
                "reply_text": "Голосовое сохранил.",
            },
        }


def test_voice_projection_is_strict_and_keeps_provider_reference_inside_gate() -> None:
    inbound = _project_inbound(
        _voice_update(),
        bot_username="@AQubAgentBot",
        received_at=datetime.fromtimestamp(1_786_000_001, tz=UTC),
    )
    assert inbound is not None
    assert inbound.content_kind is AQUBTransportContentKind.VOICE
    assert inbound.voice is not None
    assert inbound.voice.file_id == "provider-secret-file-id"
    assert inbound.command is None


@pytest.mark.anyio
async def test_voice_is_committed_then_forwarded_as_opaque_media_envelope() -> None:
    inbound = _project_inbound(
        _voice_update(),
        bot_username="@AQubAgentBot",
        received_at=datetime.fromtimestamp(1_786_000_001, tz=UTC),
    )
    assert inbound is not None
    runtime = _Runtime()
    result = await AQUBLiveIngressAdapter(
        binding=AQUBTransportBinding(
            enabled=True,
            bot_username="@AQubAgentBot",
            group_chat_id="-10042",
            ace_user_id="2002",
            alpha_user_id="1001",
            ready_at=inbound.occurred_at - timedelta(seconds=1),
            receipt_signing_key=b"k" * 32,
        ),
        store=_Store(),
        runtime=runtime,
    ).handle(inbound)
    assert result.accepted is True
    assert result.captured_content is AQUBTransportContentKind.VOICE
    envelope = json.loads(runtime.raw_body)
    assert envelope["content_kind"] == "voice"
    assert envelope["media"]["media_type"] == "audio/ogg"
    assert envelope["media"]["file_ref"].startswith("media:")
    assert "provider-secret-file-id" not in runtime.raw_body.decode()
