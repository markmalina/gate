"""Telegram voice download and bounded audio normalization adapters."""

from __future__ import annotations

import asyncio
import math
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from app.application.telegram_voice_transcription import (
    MAX_TRANSCRIPTION_PARTS,
    OPENAI_TRANSCRIPTION_LIMIT_BYTES,
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    TRANSCRIPTION_PART_SECONDS,
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
    """Split long or oversized audio; pass short bounded native files through."""

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
            suffix not in _OPENAI_NATIVE_SUFFIXES | _FFMPEG_AUDIO_SUFFIXES
            and not voice.media_type.startswith("audio/")
        ):
            raise TelegramVoiceError("telegram_voice_format_unsupported")
        needs_chunks = (
            len(voice.data) > OPENAI_TRANSCRIPTION_LIMIT_BYTES
            or (voice.duration_seconds or 0) > TRANSCRIPTION_PART_SECONDS
        )
        if (
            suffix in _OPENAI_NATIVE_SUFFIXES
            and not needs_chunks
        ):
            return NormalizedVoice(
                data=voice.data,
                filename=voice.filename,
                media_type=voice.media_type,
            )
        if needs_chunks:
            return_code, chunks, _stderr = await self._chunked_runner(voice.data)
            if return_code != 0 or not chunks:
                raise TelegramVoiceError("telegram_voice_chunking_failed")
            if len(chunks) > MAX_TRANSCRIPTION_PARTS:
                raise TelegramVoiceError("telegram_voice_too_many_parts")
            if any(
                not chunk or len(chunk) > OPENAI_TRANSCRIPTION_LIMIT_BYTES
                for chunk in chunks
            ):
                raise TelegramVoiceError("telegram_voice_normalized_too_large")
            parts = tuple(
                NormalizedVoice(
                    data=chunk,
                    filename=f"telegram-voice-part-{index:03d}.mp3",
                    media_type="audio/mpeg",
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


async def _finish_audio_process(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=120)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise


async def _probe_audio_duration(path: Path) -> float:
    """Reject empty/undecodable output even when ffmpeg exits successfully."""

    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await _finish_audio_process(process)
        duration = float(stdout.decode("ascii").strip())
    except (OSError, ValueError, TimeoutError):
        raise TelegramVoiceError("telegram_voice_audio_invalid") from None
    if process.returncode or not math.isfinite(duration) or duration <= 0:
        raise TelegramVoiceError("telegram_voice_audio_invalid")
    return duration


async def _run_ffmpeg(data: bytes) -> tuple[int, bytes, bytes]:
    # M4A needs seekable input; WAV needs a finalized, seekable output header.
    with tempfile.TemporaryDirectory(prefix="gate-audio-") as directory:
        source = Path(directory) / "source.audio"
        output = Path(directory) / "normalized.wav"
        source.write_bytes(data)
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
                "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                "-map_metadata", "-1", "-f", "wav", str(output),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await _finish_audio_process(process)
        except OSError:
            raise TelegramVoiceError("telegram_voice_normalizer_unavailable") from None
        except TimeoutError:
            raise TelegramVoiceError("telegram_voice_normalization_timeout") from None
        if process.returncode or not output.exists():
            return process.returncode or 1, b"", stderr[:4096]
        await _probe_audio_duration(output)
        if output.stat().st_size > OPENAI_TRANSCRIPTION_LIMIT_BYTES:
            raise TelegramVoiceError("telegram_voice_normalized_too_large")
        return 0, output.read_bytes(), stderr[:4096]


async def _run_ffmpeg_chunked(
    data: bytes,
) -> tuple[int, tuple[bytes, ...], bytes]:
    """Encode and validate complete, ordered three-minute mono MP3 parts."""

    with tempfile.TemporaryDirectory(prefix="gate-audio-") as directory:
        root = Path(directory)
        source = root / "source.audio"
        source.write_bytes(data)
        source_duration = await _probe_audio_duration(source)
        if source_duration > MAX_TRANSCRIPTION_PARTS * TRANSCRIPTION_PART_SECONDS:
            raise TelegramVoiceError("telegram_voice_too_many_parts")
        output_pattern = str(root / "part-%03d.mp3")
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
                "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                "-map_metadata", "-1", "-b:a", "32k", "-f", "segment",
                "-segment_time", "180", "-reset_timestamps", "1",
                "-segment_format", "mp3", output_pattern,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await _finish_audio_process(process)
        except OSError:
            raise TelegramVoiceError("telegram_voice_normalizer_unavailable") from None
        except TimeoutError:
            raise TelegramVoiceError("telegram_voice_normalization_timeout") from None
        if process.returncode:
            return process.returncode, (), stderr[:4096]
        paths = sorted(root.glob("part-*.mp3"))
        if not paths or len(paths) > MAX_TRANSCRIPTION_PARTS:
            raise TelegramVoiceError("telegram_voice_too_many_parts")
        durations = []
        for path in paths:
            if not 0 < path.stat().st_size <= OPENAI_TRANSCRIPTION_LIMIT_BYTES:
                raise TelegramVoiceError("telegram_voice_normalized_too_large")
            duration = await _probe_audio_duration(path)
            if duration > TRANSCRIPTION_PART_SECONDS + 0.2:
                raise TelegramVoiceError("telegram_voice_part_duration_invalid")
            durations.append(duration)
        # MP3 frame padding adds up to ~0.15 seconds per standalone part.
        if abs(sum(durations) - source_duration) > max(0.5, len(paths) * 0.2):
            raise TelegramVoiceError("telegram_voice_audio_coverage_mismatch")
        return 0, tuple(path.read_bytes() for path in paths), stderr[:4096]
