"""Regression tests for the exact scoped Telegram voice export selector."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.api.mcp_routes import (
    _extract_voice_from_linked_payloads,
    _parse_latest_voice_file_selector,
    _parse_latest_voice_selector,
    _parse_latest_voice_transcript_selector,
    _parse_reply_context_selector,
    _same_telegram_message,
    _voice_audio_tool_result,
    _voice_file_tool_result,
)

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = UUID("22222222-2222-2222-2222-222222222222")


def test_latest_voice_selector_binds_one_account_alias() -> None:
    assert _parse_latest_voice_selector("voice:latest:ru") == "ru"
    assert _parse_latest_voice_selector(str(TASK_ID)) is None


@pytest.mark.parametrize(
    "selector",
    (
        "voice:latest:",
        "voice:latest:RU",
        "voice:latest:ru/account",
    ),
)
def test_invalid_latest_voice_selector_fails_closed(selector: str) -> None:
    with pytest.raises(ValueError):
        _parse_latest_voice_selector(selector)


def test_latest_voice_file_selector_binds_one_account_alias() -> None:
    assert (
        _parse_latest_voice_file_selector(
            "voice:latest-file:telegram_mark_ru"
        )
        == "telegram_mark_ru"
    )
    assert _parse_latest_voice_file_selector(str(TASK_ID)) is None
    assert _parse_latest_voice_selector(
        "voice:latest-file:telegram_mark_ru"
    ) is None


@pytest.mark.parametrize(
    "selector",
    (
        "voice:latest-file:",
        "voice:latest-file:Telegram",
        "voice:latest-file:telegram.mark",
    ),
)
def test_invalid_latest_voice_file_selector_fails_closed(
    selector: str,
) -> None:
    with pytest.raises(ValueError):
        _parse_latest_voice_file_selector(selector)


def test_latest_voice_transcript_selector_binds_one_account_alias() -> None:
    assert (
        _parse_latest_voice_transcript_selector(
            "voice:latest-transcript:telegram_mark_ru"
        )
        == "telegram_mark_ru"
    )
    assert _parse_latest_voice_transcript_selector(str(TASK_ID)) is None
    assert _parse_latest_voice_selector(
        "voice:latest-transcript:telegram_mark_ru"
    ) is None


@pytest.mark.parametrize(
    "selector",
    (
        "voice:latest-transcript:",
        "voice:latest-transcript:Telegram",
        "voice:latest-transcript:telegram.mark",
    ),
)
def test_invalid_latest_voice_transcript_selector_fails_closed(
    selector: str,
) -> None:
    with pytest.raises(ValueError):
        _parse_latest_voice_transcript_selector(selector)


def test_plain_reply_context_selector_preserves_existing_behavior() -> None:
    assert _parse_reply_context_selector(str(TASK_ID)) == (
        TASK_ID,
        None,
        None,
    )


def test_exact_voice_selector_binds_task_and_message() -> None:
    selector = f"{TASK_ID}:voice:{MESSAGE_ID}"
    assert _parse_reply_context_selector(selector) == (
        TASK_ID,
        MESSAGE_ID,
        None,
    )


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

    reference = _extract_voice_from_linked_payloads({}, payload)

    assert reference is not None
    assert reference.file_unique_id == "opaque-unique"
    assert reference.declared_size_bytes == 128
    assert reference.duration_seconds == 3


@pytest.mark.parametrize(
    "payload",
    (
        {
            "audio": {
                "file_id": "opaque-file",
                "file_unique_id": "opaque-unique",
                "file_size": 128,
                "duration": 3,
            },
        },
        {
            "edited_business_message": {
                "document": {
                    "file_id": "opaque-file",
                    "file_unique_id": "opaque-unique",
                    "file_size": 128,
                    "duration": 3,
                    "mime_type": "audio/mpeg",
                },
            },
        },
        {
            "result": {
                "voice": {
                    "file_id": "opaque-file",
                    "file_unique_id": "opaque-unique",
                    "file_size": 128,
                    "duration": 3,
                },
            },
        },
    ),
)
def test_linked_audio_payload_accepts_durable_telegram_shapes(
    payload: dict[str, object],
) -> None:
    reference = _extract_voice_from_linked_payloads(payload)

    assert reference is not None
    assert reference.file_unique_id == "opaque-unique"


def test_exact_telegram_identity_matches_message_and_chat() -> None:
    message = SimpleNamespace(telegram_message_id=20, chat_id="30")

    assert _same_telegram_message(
        raw_message={"message_id": 20, "chat": {"id": 30}},
        message=message,
    )
    assert not _same_telegram_message(
        raw_message={"message_id": 21, "chat": {"id": 30}},
        message=message,
    )
    assert not _same_telegram_message(
        raw_message={"message_id": 20, "chat": {"id": 31}},
        message=message,
    )


def test_linked_voice_payload_fails_closed_without_audio() -> None:
    assert _extract_voice_from_linked_payloads({}, None) is None
    assert (
        _extract_voice_from_linked_payloads(
            {
                "document": {
                    "file_id": "opaque-file",
                    "file_unique_id": "opaque-unique",
                    "file_size": 128,
                    "duration": 3,
                    "mime_type": "application/pdf",
                },
            }
        )
        is None
    )


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


def test_voice_file_result_emits_connector_safe_base64_json() -> None:
    context = {
        "task_id": str(TASK_ID),
        "messages": [],
    }
    payload = _voice_file_tool_result(
        request_id="voice-file-1",
        context=context,
        archive_id=str(TASK_ID),
        data=b"voice-bytes",
        media_type="audio/ogg",
    )
    result = payload["result"]
    file_payload = json.loads(result["content"][0]["text"])

    assert result["structuredContent"] is context
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert file_payload == {
        "version": 1,
        "filename": f"telegram-voice-{TASK_ID}.ogg",
        "mime_type": "audio/ogg",
        "data_base64": base64.b64encode(b"voice-bytes").decode("ascii"),
    }
