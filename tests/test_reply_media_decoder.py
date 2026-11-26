"""Run isolated child processes to verify decoder resource and cancellation bounds."""

import asyncio
import signal
import sys
from contextlib import suppress

import pytest
from app.application import reply_media_decoder as decoder


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sleeping_decoders(monkeypatch) -> list[asyncio.subprocess.Process]:
    original_spawn = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        process = await original_spawn(
            sys.executable, "-I", "-c", "import time; time.sleep(30)", **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return processes


@pytest.mark.anyio
async def test_timeout_kills_and_reaps_decoder(sleeping_decoders, monkeypatch) -> None:
    monkeypatch.setattr(decoder, "DECODE_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(decoder.ImageDecodeError) as error:
        await decoder.decode_image(b"fixture", "image/png")
    assert error.value.code == "media_validation_timeout"
    assert len(sleeping_decoders) == 1
    assert sleeping_decoders[0].returncode == -signal.SIGKILL


@pytest.mark.anyio
async def test_no_decoder_queue_and_cancellation_releases_slots(sleeping_decoders) -> None:
    tasks = [
        asyncio.create_task(decoder.decode_image(b"fixture", "image/png"))
        for _ in range(decoder.DECODE_SLOTS)
    ]
    try:
        # Let each task claim a slot. One may still be awaiting its spawn.
        await asyncio.sleep(0)
        with pytest.raises(decoder.ImageDecodeError) as error:
            await decoder.decode_image(b"fixture", "image/png")
        assert error.value.code == "media_validation_busy"
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    assert len(sleeping_decoders) == decoder.DECODE_SLOTS
    assert all(process.returncode == -signal.SIGKILL for process in sleeping_decoders)


@pytest.mark.anyio
async def test_os_memory_and_cpu_limits_are_active_before_decode() -> None:
    # Observe the real worker at its decoder boundary, after setrlimit and
    # before any image code runs. Verify no unbounded limit is accepted.
    child_code = """
import resource
import runpy
import sys

worker = runpy.run_path(sys.argv[1])
def decode(data, media_type):
    assert resource.getrlimit(resource.RLIMIT_AS) == (int(sys.argv[2]),) * 2
    assert resource.getrlimit(resource.RLIMIT_CPU) == (int(sys.argv[3]),) * 2
    return worker["DecodedImage"](b"x", 1, 1, 1, "RGB")
worker["_worker"].__globals__["_decode"] = decode
worker["_worker"]("image/png")
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        child_code,
        decoder.__file__,
        str(decoder.DECODE_MEMORY_BYTES),
        str(decoder.DECODE_CPU_SECONDS),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, _ = await asyncio.wait_for(process.communicate(b"fixture"), timeout=5)
    assert process.returncode == 0
    assert output == b"\x00" + decoder._HEADER.pack(1, 1, 1, 1, 0) + b"x"
