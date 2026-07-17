"""Pure Telegram update extraction and text projection for voice transcription."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.application.telegram_voice_transcription import (
    TelegramVoiceError,
    TelegramVoiceReference,
)

JsonObject = dict[str, Any]
_MESSAGE_KEYS = ("message", "business_message")


@dataclass(frozen=True, slots=True)
class TelegramVoiceEnvelope:
    update_id: int
    message_key: str
    message_id: int
    voice: TelegramVoiceReference
    source_ref: str = field(repr=False)


def extract_telegram_voice(update: JsonObject) -> TelegramVoiceEnvelope | None:
    """Return one validated voice envelope without mutating the durable receipt."""

    messages = [
        (key, update.get(key))
        for key in _MESSAGE_KEYS
        if isinstance(update.get(key), dict)
    ]
    if not messages:
        return None
    if len(messages) != 1:
        raise TelegramVoiceError("telegram_voice_update_ambiguous")
    message_key, raw_message = messages[0]
    if not isinstance(raw_message, dict):
        raise TelegramVoiceError("telegram_voice_message_invalid")
    raw_voice = raw_message.get("voice")
    if raw_voice is None:
        return None
    if not isinstance(raw_voice, dict):
        raise TelegramVoiceError("telegram_voice_payload_invalid")

    update_id = _positive_int(update.get("update_id"), "telegram_voice_update_id_invalid")
    message_id = _positive_int(
        raw_message.get("message_id"),
        "telegram_voice_message_id_invalid",
    )
    file_id = _string(raw_voice.get("file_id"), "telegram_voice_file_id_invalid")
    file_unique_id = _string(
        raw_voice.get("file_unique_id"),
        "telegram_voice_unique_id_invalid",
    )
    file_size = raw_voice.get("file_size")
    if file_size is not None and (type(file_size) is not int or file_size <= 0):
        raise TelegramVoiceError("telegram_voice_size_invalid")
    duration = _positive_int(
        raw_voice.get("duration"),
        "telegram_voice_duration_invalid",
    )
    return TelegramVoiceEnvelope(
        update_id=update_id,
        message_key=message_key,
        message_id=message_id,
        voice=TelegramVoiceReference(
            file_id=file_id,
            file_unique_id=file_unique_id,
            declared_size_bytes=file_size,
            duration_seconds=duration,
        ),
        source_ref=f"telegram:{message_key}:{update_id}:{message_id}",
    )


def project_transcribed_voice(
    update: JsonObject,
    *,
    envelope: TelegramVoiceEnvelope,
    transcript: str,
) -> JsonObject:
    """Replace voice metadata with text in a copy suitable for canonical/Alpha routing."""

    if not transcript or transcript != transcript.strip() or len(transcript) > 100_000:
        raise TelegramVoiceError("telegram_voice_transcript_invalid")
    current = extract_telegram_voice(update)
    if current != envelope:
        raise TelegramVoiceError("telegram_voice_projection_identity_mismatch")

    projected = deepcopy(update)
    message = projected.get(envelope.message_key)
    if not isinstance(message, dict):
        raise TelegramVoiceError("telegram_voice_projection_message_missing")
    message.pop("voice", None)
    message["text"] = transcript
    message["gate_input_origin"] = "telegram_voice_transcription"
    return projected


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise TelegramVoiceError(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TelegramVoiceError(code)
    return value
