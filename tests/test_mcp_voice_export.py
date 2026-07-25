"""Regression tests for the exact scoped Telegram voice export selector."""

from __future__ import annotations

import base64
from uuid import UUID

import pytest
from app.api.mcp_routes import (
    _extract_voice_from_linked_payloads,
    _parse_reply_context_selector,
    _voice_audio_tool_result,
)

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = UUID("22222222-2222-2222-2222-222222222222")


def test_plain_reply_context_selector_preserves_existing_behavior() -> None:
    assert _parse_reply_context_selector(str(TASK_ID)) == (TASK_ID, None)


def test_exact_voice_selector_binds_task_and_message() -> None:
    selector = f"{TASK_ID}:voice:{MESSAGE_ID}"
    assert _parse_reply_context_selector(selector) == (TASK_ID, MESSAGE_ID)


@pytest.mark.parametrize(
    "selector",
    (
        "not-a-uuid",
        f"{TASK_ID}:voice:",
        f":voice:{MESSAGE_ID}",
        f"{TASK_ID}:voice:{MESSAGE_ID}:voice:{MESSAGE_ID}",
    ),
)
def test_invalid_voice_selector_fails_closed(selector: str) -> None:
    with pytest.raises(ValueError):
        _parse_reply_context_selector(selector)


def test_linked_voice_payload_falls_back_to_the_exact_durable_update() -> None:
    payload = {
        "update_id": 10,
        "business_message": {
            "message_id": 20,
            "voice": {
                "file_id": "opaque-file",
                "file_unique_id": "opaque-unique",
                "file_size": 128,
                "duration": 3,
            },
        },
    }

    envelope = _extract_voice_from_linked_payloads({}, payload)

    assert envelope is not None
    assert envelope.update_id == 10
    assert envelope.message_id == 20


def test_linked_voice_payload_fails_closed_without_voice() -> None:
    assert _extract_voice_from_linked_payloads({}, None) is None


def test_voice_audio_result_keeps_context_and_emits_mcp_audio() -> None:
    context = {
        "task_id": str(TASK_ID),
        "messages": [{"id": str(MESSAGE_ID)}],
    }
    payload = _voice_audio_tool_result(
        request_id="voice-1",
        context=context,
        data=b"voice-bytes",
        media_type="audio/ogg",
    )
    result = payload["result"]

    assert result["structuredContent"] is context
    assert result["isError"] is False
    assert result["content"][0] == {
        "type": "text",
        "text": "Owner-selected Telegram voice attached.",
    }
    assert result["content"][1] == {
        "type": "audio",
        "data": base64.b64encode(b"voice-bytes").decode("ascii"),
        "mimeType": "audio/ogg",
    }


@pytest.mark.parametrize(
    ("data", "media_type"),
    ((b"", "audio/ogg"), (b"voice", "application/octet-stream")),
)
def test_voice_audio_result_rejects_invalid_content(
    data: bytes,
    media_type: str,
) -> None:
    with pytest.raises(ValueError):
        _voice_audio_tool_result(
            request_id="voice-2",
            context={},
            data=data,
            media_type=media_type,
        )
