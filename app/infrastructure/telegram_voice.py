"""Telegram voice download and bounded audio normalization adapters."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from app.application.telegram_voice_transcription import (
    OPENAI_TRANSCRIPTION_LIMIT_BYTES,
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    DownloadedVoice,
    NormalizedVoice,
    TelegramVoiceError,
    TelegramVoiceReference,
)


@dataclass(frozen=True, slots=True)
class TelegramVoiceDownloadConfig:
    bot_token: str = field(repr=False)
    api_base_url: str = "https://api.telegram.org"

    def __post_init__(self) -> None:
        if not self.bot_token:
            raise ValueError("telegram_voice_bot_token_required")
        if not self.api_base_url.startswith("https://"):
            raise ValueError("telegram_voice_api_url_invalid")


class TelegramVoiceDownloader:
    def __init__(
        self,
        *,
        config: TelegramVoiceDownloadConfig,
        http: httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._http = http

    async def download(self, voice: TelegramVoiceReference) -> DownloadedVoice:
        api = self._config.api_base_url.rstrip("/")
        try:
            metadata_response = await self._http.post(
                f"{api}/bot{self._config.bot_token}/getFile",
                json={"file_id": voice.file_id},
            )
            metadata = metadata_response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramVoiceError("telegram_voice_download_failed") from None
        result = _telegram_result(metadata_response, metadata)
        file_size = result.get("file_size")
        if isinstance(file_size, int) and file_size > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
            raise TelegramVoiceError("telegram_voice_download_limit_exceeded")
        file_path = result.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise TelegramVoiceError("telegram_voice_file_path_missing")
        path = PurePosixPath(file_path.strip().lstrip("/"))
        if any(part in {".", ".."} for part in path.parts):
            raise TelegramVoiceError("telegram_voice_file_path_invalid")
        safe_path = quote(path.as_posix(), safe="/")
        try:
            data = bytearray()
            media_type = "audio/ogg"
            async with self._http.stream(
                "GET",
                f"{api}/file/bot{self._config.bot_token}/{safe_path}",
            ) as response:
                response.raise_for_status()
                media_type = response.headers.get("content-type", media_type).split(
                    ";", 1
                )[0]
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
                        raise TelegramVoiceError(
                            "telegram_voice_download_limit_exceeded"
                        )
        except httpx.HTTPError:
            raise TelegramVoiceError("telegram_voice_download_failed") from None
        return DownloadedVoice(
            data=bytes(data),
            filename=PurePosixPath(file_path).name or "telegram-voice.ogg",
            media_type=media_type,
        )


def _telegram_result(response: httpx.Response, payload: Any) -> dict[str, Any]:
    if response.is_error or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramVoiceError("telegram_voice_get_file_rejected")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TelegramVoiceError("telegram_voice_get_file_invalid")
    return result


FfmpegRunner = Callable[[bytes], Awaitable[tuple[int, bytes, bytes]]]
ChunkedFfmpegRunner = Callable[
    [bytes], Awaitable[tuple[int, tuple[bytes, ...], bytes]]
]
_OPENAI_NATIVE_SUFFIXES = {
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".m4a",
    ".wav",
    ".webm",
}
_FFMPEG_AUDIO_SUFFIXES = {
    ".3gp",
    ".aac",
    ".amr",
    ".flac",
    ".oga",
    ".ogg",
    ".opus",
}


class TelegramVoiceNormalizer:
    """Pass bounded native audio through and compress provider-oversized sources."""

    def __init__(
        self,
        runner: FfmpegRunner | None = None,
        chunked_runner: ChunkedFfmpegRunner | None = None,
    ) -> None:
        self._runner = runner or _run_ffmpeg
        self._chunked_runner = chunked_runner or _run_ffmpeg_chunked

    async def normalize(self, voice: DownloadedVoice) -> NormalizedVoice:
        suffix = PurePosixPath(voice.filename).suffix.lower()
        if (
            suffix in _OPENAI_NATIVE_SUFFIXES
            and len(voice.data) <= OPENAI_TRANSCRIPTION_LIMIT_BYTES
        ):
            return NormalizedVoice(
                data=voice.data,
                filename=voice.filename,
                media_type=voice.media_type,
            )
        if suffix in _OPENAI_NATIVE_SUFFIXES:
            return_code, chunks, _stderr = await self._chunked_runner(voice.data)
            if return_code != 0 or not chunks:
                raise TelegramVoiceError("telegram_voice_chunking_failed")
            if len(chunks) > 10:
                raise TelegramVoiceError("telegram_voice_too_many_parts")
            if any(
                not chunk or len(chunk) > OPENAI_TRANSCRIPTION_LIMIT_BYTES
                for chunk in chunks
            ):
                raise TelegramVoiceError("telegram_voice_normalized_too_large")
            parts = tuple(
                NormalizedVoice(
                    data=chunk,
                    filename=f"telegram-voice-part-{index:03d}.wav",
                    media_type="audio/wav",
                )
                for index, chunk in enumerate(chunks, start=1)
            )
            return NormalizedVoice(
                data=parts[0].data,
                filename=parts[0].filename,
                media_type=parts[0].media_type,
                additional_parts=parts[1:],
            )
        if suffix not in _FFMPEG_AUDIO_SUFFIXES and not voice.media_type.startswith(
            "audio/"
        ):
            raise TelegramVoiceError("telegram_voice_format_unsupported")
        return_code, stdout, _stderr = await self._runner(voice.data)
        if return_code != 0 or not stdout:
            raise TelegramVoiceError("telegram_voice_normalization_failed")
        if len(stdout) > OPENAI_TRANSCRIPTION_LIMIT_BYTES:
            raise TelegramVoiceError("telegram_voice_normalized_too_large")
        return NormalizedVoice(
            data=stdout,
            filename="telegram-voice.wav",
            media_type="audio/wav",
        )


async def _run_ffmpeg(data: bytes) -> tuple[int, bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        raise TelegramVoiceError("telegram_voice_normalizer_unavailable") from None
    stdout, stderr = await process.communicate(data)
    return process.returncode or 0, stdout, stderr[:4096]


async def _run_ffmpeg_chunked(
    data: bytes,
) -> tuple[int, tuple[bytes, ...], bytes]:
    """Encode bounded five-minute mono PCM WAV parts for the provider."""

    with tempfile.TemporaryDirectory(prefix="gate-audio-") as directory:
        output_pattern = str(Path(directory) / "part-%03d.wav")
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "segment",
                "-segment_time",
                "300",
                "-reset_timestamps",
                "1",
                "-segment_format",
                "wav",
                output_pattern,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            raise TelegramVoiceError("telegram_voice_normalizer_unavailable") from None
        _stdout, stderr = await process.communicate(data)
        chunks = tuple(
            path.read_bytes()
            for path in sorted(Path(directory).glob("part-*.wav"))
            if path.stat().st_size > 0
        )
        return process.returncode or 0, chunks, stderr[:4096]
