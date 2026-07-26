import asyncio
from pathlib import Path

import pytest

from app.application.telegram_voice_transcription import (
    OPENAI_TRANSCRIPTION_LIMIT_BYTES,
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    DownloadedVoice,
    TelegramVoiceError,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer


def test_provider_oversized_native_audio_is_compressed() -> None:
    calls: list[int] = []

    async def compressed(data: bytes) -> tuple[int, bytes, bytes]:
        calls.append(len(data))
        return 0, b"bounded-mp3", b""

    normalizer = TelegramVoiceNormalizer(compressed_runner=compressed)
    source = DownloadedVoice(
        data=b"x" * (OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1),
        filename="call.m4a",
        media_type="audio/mp4",
    )

    result = asyncio.run(normalizer.normalize(source))

    assert calls == [OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1]
    assert result.data == b"bounded-mp3"
    assert result.filename == "telegram-voice.mp3"
    assert result.media_type == "audio/mpeg"


def test_native_audio_at_provider_limit_is_not_reencoded() -> None:
    async def unexpected(_data: bytes) -> tuple[int, bytes, bytes]:
        raise AssertionError("compression must not run")

    normalizer = TelegramVoiceNormalizer(compressed_runner=unexpected)
    source = DownloadedVoice(
        data=b"x" * OPENAI_TRANSCRIPTION_LIMIT_BYTES,
        filename="call.m4a",
        media_type="audio/mp4",
    )

    result = asyncio.run(normalizer.normalize(source))

    assert result.data is source.data
    assert result.filename == "call.m4a"


def test_compression_must_finish_under_provider_limit() -> None:
    async def still_large(_data: bytes) -> tuple[int, bytes, bytes]:
        return 0, b"x" * (OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1), b""

    normalizer = TelegramVoiceNormalizer(compressed_runner=still_large)
    source = DownloadedVoice(
        data=b"x" * (OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1),
        filename="call.m4a",
        media_type="audio/mp4",
    )

    with pytest.raises(TelegramVoiceError, match="telegram_voice_normalized_too_large"):
        asyncio.run(normalizer.normalize(source))


def test_historical_call_fits_archive_but_requires_compression() -> None:
    size = 25_505_021
    assert OPENAI_TRANSCRIPTION_LIMIT_BYTES < size <= TELEGRAM_DOWNLOAD_LIMIT_BYTES


def test_exact_historical_call_is_requeued_by_migration() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0054_audio_provider_limit.py"
    ).read_text(encoding="utf-8")

    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "size_bytes = 25505021" in migration
    assert "openai_transcription_rejected" in migration
