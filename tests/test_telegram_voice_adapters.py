"""Telegram download limits and OGG normalization are enforced before OpenAI."""

import httpx
import pytest
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    TelegramVoiceError,
    TelegramVoiceReference,
)
from app.infrastructure.telegram_voice import (
    TelegramVoiceDownloadConfig,
    TelegramVoiceDownloader,
    TelegramVoiceNormalizer,
)


def reference() -> TelegramVoiceReference:
    return TelegramVoiceReference(
        file_id="file-secret",
        file_unique_id="unique-secret",
        declared_size_bytes=9,
        duration_seconds=2,
    )


@pytest.mark.anyio
async def test_download_uses_get_file_then_bounded_file_url() -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_size": 9,
                        "file_path": "voice/file_1.oga",
                    },
                },
            )
        return httpx.Response(
            200,
            content=b"ogg-bytes",
            headers={"content-type": "audio/ogg"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        downloader = TelegramVoiceDownloader(
            config=TelegramVoiceDownloadConfig(bot_token="bot-secret"),
            http=http,
        )
        result = await downloader.download(reference())

    assert result.data == b"ogg-bytes"
    assert result.filename == "file_1.oga"
    assert paths == [
        "/botbot-secret/getFile",
        "/file/botbot-secret/voice/file_1.oga",
    ]
    assert "bot-secret" not in repr(downloader._config)


@pytest.mark.anyio
async def test_ogg_voice_is_converted_to_documented_wav_input() -> None:
    received: list[bytes] = []

    async def runner(data: bytes) -> tuple[int, bytes, bytes]:
        received.append(data)
        return 0, b"wav-bytes", b""

    result = await TelegramVoiceNormalizer(runner).normalize(
        DownloadedVoice(b"ogg-bytes", "voice.oga", "audio/ogg")
    )

    assert received == [b"ogg-bytes"]
    assert result.filename == "telegram-voice.wav"
    assert result.media_type == "audio/wav"


@pytest.mark.anyio
async def test_failed_conversion_does_not_forward_original_ogg() -> None:
    async def runner(_: bytes) -> tuple[int, bytes, bytes]:
        return 1, b"", b"decoder details"

    with pytest.raises(
        TelegramVoiceError,
        match="telegram_voice_normalization_failed",
    ):
        await TelegramVoiceNormalizer(runner).normalize(
            DownloadedVoice(b"ogg-bytes", "voice.ogg", "audio/ogg")
        )

