"""Real-media regression for the zero-audio M4A pipe failure (no provider I/O)."""

import asyncio
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest
from app.application.telegram_voice_transcription import DownloadedVoice, TelegramVoiceError
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer, _probe_audio_duration


def test_tail_moov_m4a_chunks_have_complete_ordered_audio(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    source = tmp_path / "source.m4a"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=181", "-f", "lavfi", "-i",
            "sine=frequency=880:duration=11", "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1[out]", "-map", "[out]",
            "-c:a", "aac", "-b:a", "96k", str(source),
        ],
        check=True, capture_output=True,
    )
    raw = source.read_bytes()
    assert raw.index(b"mdat") < raw.index(b"moov")
    assert len(raw) < 25_000_000  # Duration, not just size, must trigger splitting.
    voice = asyncio.run(TelegramVoiceNormalizer().normalize(
        DownloadedVoice(raw, "source.m4a", "audio/mp4", duration_seconds=192)
    ))
    parts = (voice, *voice.additional_parts)
    assert len(parts) == 2
    durations = []
    frequencies = []
    for index, part in enumerate(parts):
        path = tmp_path / f"part-{index}.mp3"
        path.write_bytes(part.data)
        assert 400 < len(part.data) < 1_000_000
        durations.append(asyncio.run(_probe_audio_duration(path)))
        # Decode the last second; the final source tone proves the tail survived.
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-sseof", "-2", "-i", str(path),
             "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        samples = array("h", pcm)[8000:16000]
        assert max(abs(value) for value in samples) > 100
        crossings = sum((a >= 0) != (b >= 0) for a, b in zip(samples, samples[1:], strict=False))
        frequencies.append(crossings / (2 * len(samples) / 16000))
    assert abs(sum(durations) - 192) < 0.5
    assert all(duration <= 180.2 for duration in durations)
    assert frequencies[0] == pytest.approx(440, abs=15)
    assert frequencies[1] == pytest.approx(880, abs=20)
    assert not list(tmp_path.glob("gate-audio-*"))


def test_empty_header_is_rejected_even_if_ffprobe_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 0

        async def communicate(self):
            return b"N/A\n", b""

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(TelegramVoiceError, match="telegram_voice_audio_invalid"):
        asyncio.run(_probe_audio_duration(tmp_path / "header.mp3"))


@pytest.mark.parametrize("suffix,media_type", [("ogg", "audio/ogg"), ("mp3", "audio/mpeg")])
def test_long_compressed_audio_uses_duration_bound(suffix, media_type) -> None:
    async def chunks(_data):
        return 0, (b"first", b"last"), b""

    result = asyncio.run(TelegramVoiceNormalizer(chunked_runner=chunks).normalize(
        DownloadedVoice(b"small compressed source", f"voice.{suffix}", media_type, 190)
    ))
    assert len(result.additional_parts) == 1
    assert result.additional_parts[0].data == b"last"
