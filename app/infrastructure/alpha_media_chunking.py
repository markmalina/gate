"""FFmpeg/ffprobe boundary for deterministic overlapping transcription chunks."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.alpha_transcription_product import (
    CHUNK_MILLIS,
    MAX_SOURCE_BYTES,
    AlphaTranscriptionError,
    PartWindow,
    build_part_windows,
    sha256_bytes,
)
from app.application.alpha_transcription_receipts import (
    preexisting_receipt_for_source,
)


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    window: PartWindow
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    duration_millis: int
    chunks: tuple[PreparedChunk, ...]


async def prepare_media_chunks(source: Path, output_dir: Path) -> PreparedMedia:
    size = source.stat().st_size
    if size <= 0:
        raise AlphaTranscriptionError("alpha_transcription_source_empty")
    if size > MAX_SOURCE_BYTES:
        raise AlphaTranscriptionError("alpha_transcription_source_too_large")
    source_sha256 = await asyncio.to_thread(_sha256_file, source)
    if preexisting_receipt_for_source(source_sha256) is not None:
        raise AlphaTranscriptionError(
            "alpha_transcription_preexisting_receipt_requires_import"
        )
    duration = await probe_duration_millis(source)
    windows = build_part_windows(duration)
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[PreparedChunk] = []
    for window in windows:
        destination = output_dir / f"part-{window.index:04d}.mp3"
        await encode_window(source, destination, window)
        data = destination.read_bytes()
        if not data or len(data) > 25_000_000:
            raise AlphaTranscriptionError("alpha_transcription_part_size_invalid")
        chunks.append(
            PreparedChunk(
                window=window,
                path=destination,
                sha256=sha256_bytes(data),
                size_bytes=len(data),
            )
        )
    return PreparedMedia(duration_millis=duration, chunks=tuple(chunks))


async def probe_duration_millis(source: Path) -> int:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        raise AlphaTranscriptionError("alpha_transcription_ffprobe_unavailable") from None
    stdout, _stderr = await process.communicate()
    if process.returncode != 0:
        raise AlphaTranscriptionError("alpha_transcription_media_probe_failed")
    try:
        payload: Any = json.loads(stdout)
        duration_value = payload["format"]["duration"]
        duration_millis = round(float(duration_value) * 1000)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise AlphaTranscriptionError("alpha_transcription_media_duration_invalid") from None
    if duration_millis <= 0:
        raise AlphaTranscriptionError("alpha_transcription_media_duration_invalid")
    return duration_millis


async def encode_window(source: Path, destination: Path, window: PartWindow) -> None:
    start_seconds = f"{window.start_millis / 1000:.3f}"
    duration_seconds = f"{(window.end_millis - window.start_millis) / 1000:.3f}"
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            start_seconds,
            "-i",
            str(source),
            "-t",
            duration_seconds,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "32k",
            "-f",
            "mp3",
            "-y",
            str(destination),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        raise AlphaTranscriptionError("alpha_transcription_ffmpeg_unavailable") from None
    _stdout, _stderr = await process.communicate()
    if process.returncode != 0 or not destination.exists():
        raise AlphaTranscriptionError("alpha_transcription_chunking_failed")
    if window.index > 0 and window.end_millis - window.start_millis > CHUNK_MILLIS:
        raise AlphaTranscriptionError("alpha_transcription_chunk_window_invalid")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
