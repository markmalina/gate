"""Shared FFmpeg commands and readers for seekable audio duration checks."""

import asyncio
import math
from pathlib import Path

MAX_DECODED_AUDIO_SECONDS = 24 * 60 * 60


async def start_duration_probe(path: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )


async def start_duration_decode(path: Path) -> asyncio.subprocess.Process:
    """Read the last decoded timestamp without writing a second media file."""
    return await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-xerror", "-i", str(path), "-map", "0:a:0", "-vn",
        "-f", "null", "-progress", "pipe:1", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )


def container_duration_seconds(output: bytes) -> float | None:
    try:
        duration = float(output.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


def decoded_duration_microseconds(output: bytes) -> int | None:
    prefix = b"out_time_us="
    timestamps = [
        line.removeprefix(prefix) for line in output.splitlines() if line.startswith(prefix)
    ]
    try:
        duration = int(timestamps[-1])
    except (IndexError, ValueError):
        return None
    return duration if 0 < duration <= MAX_DECODED_AUDIO_SECONDS * 1_000_000 else None
