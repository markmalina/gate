from __future__ import annotations

from pathlib import Path

import pytest

from app.application.alpha_transcription_product import AlphaTranscriptionError
from app.application.alpha_transcription_receipts import (
    preexisting_receipt_for_source,
)
from app.infrastructure import alpha_media_chunking

_CHERNIGOVSKAYA_SOURCE_SHA = (
    "c400f62b24429ae38d755632f81fa945624749b5cfaf8adb2744d431747ca5a9"
)


def test_chernigovskaya_receipt_blocks_a_second_paid_pass() -> None:
    receipt = preexisting_receipt_for_source(_CHERNIGOVSKAYA_SOURCE_SHA)

    assert receipt is not None
    assert receipt.transcript_sha256 == (
        "3d59b7d3f2c24fe6127a85ea503a838967e69fed8f429c2654151fa8b5d98891"
    )
    assert receipt.duration_millis == 1_350_635
    assert receipt.provider_attempts == 6
    assert receipt.billed_quantity == 18_049
    assert receipt.actual_microusd == 38_660


def test_unknown_source_has_no_preexisting_receipt() -> None:
    assert preexisting_receipt_for_source("0" * 64) is None


@pytest.mark.anyio
async def test_preexisting_receipt_stops_before_media_or_provider_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "known.m4a"
    source.write_bytes(b"known-paid-source")

    monkeypatch.setattr(
        alpha_media_chunking,
        "_sha256_file",
        lambda _path: _CHERNIGOVSKAYA_SOURCE_SHA,
    )

    async def unexpected_probe(_source: Path) -> int:
        raise AssertionError("ffprobe must not run for an already-paid source")

    monkeypatch.setattr(
        alpha_media_chunking,
        "probe_duration_millis",
        unexpected_probe,
    )

    with pytest.raises(AlphaTranscriptionError) as captured:
        await alpha_media_chunking.prepare_media_chunks(source, tmp_path / "parts")

    assert captured.value.code == (
        "alpha_transcription_preexisting_receipt_requires_import"
    )
