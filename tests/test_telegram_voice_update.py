"""Only text crosses from Gate voice transcription into downstream routing."""

import pytest
from app.application.telegram_voice_transcription import TelegramVoiceError
from app.application.telegram_voice_update import (
    extract_telegram_voice,
    project_transcribed_voice,
)


def update(key: str = "message") -> dict[str, object]:
    return {
        "update_id": 101,
        key: {
            "message_id": 202,
            "date": 1_700_000_000,
            "chat": {"id": -303, "type": "supergroup"},
            "from": {"id": 404, "is_bot": False},
            "voice": {
                "file_id": "secret-download-id",
                "file_unique_id": "secret-stable-id",
                "file_size": 1234,
                "duration": 12,
                "mime_type": "audio/ogg",
            },
        },
    }


@pytest.mark.parametrize("message_key", ["message", "business_message"])
def test_extracts_supported_telegram_voice_shapes(message_key: str) -> None:
    envelope = extract_telegram_voice(update(message_key))

    assert envelope is not None
    assert envelope.message_key == message_key
    assert envelope.update_id == 101
    assert envelope.message_id == 202
    assert envelope.voice.duration_seconds == 12
    assert envelope.voice.declared_size_bytes == 1234
    assert "secret-download-id" not in repr(envelope)
    assert "secret-stable-id" not in repr(envelope)


def test_projection_removes_voice_reference_and_preserves_original() -> None:
    original = update()
    envelope = extract_telegram_voice(original)
    assert envelope is not None

    projected = project_transcribed_voice(
        original,
        envelope=envelope,
        transcript="Распознанный текст",
    )

    assert "voice" in original["message"]
    message = projected["message"]
    assert isinstance(message, dict)
    assert "voice" not in message
    assert message["text"] == "Распознанный текст"
    assert message["gate_input_origin"] == "telegram_voice_transcription"
    assert message["chat"] == {"id": -303, "type": "supergroup"}


def test_text_message_is_not_claimed_by_voice_path() -> None:
    assert (
        extract_telegram_voice(
            {
                "update_id": 1,
                "message": {"message_id": 2, "text": "обычный текст"},
            }
        )
        is None
    )


def test_projection_rejects_mismatched_durable_identity() -> None:
    original = update()
    envelope = extract_telegram_voice(original)
    assert envelope is not None
    changed = update()
    changed["update_id"] = 999

    with pytest.raises(
        TelegramVoiceError,
        match="telegram_voice_projection_identity_mismatch",
    ):
        project_transcribed_voice(
            changed,
            envelope=envelope,
            transcript="Распознанный текст",
        )

