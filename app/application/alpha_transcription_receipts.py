"""Pre-product provider receipts that must never be charged again."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreexistingTranscriptionReceipt:
    source_sha256: str
    transcript_sha256: str
    duration_millis: int
    model: str
    provider_attempts: int
    billed_quantity: int
    actual_microusd: int


_CHERNIGOVSKAYA_RECEIPT = PreexistingTranscriptionReceipt(
    source_sha256="c400f62b24429ae38d755632f81fa945624749b5cfaf8adb2744d431747ca5a9",
    transcript_sha256="3d59b7d3f2c24fe6127a85ea503a838967e69fed8f429c2654151fa8b5d98891",
    duration_millis=1_350_635,
    model="gpt-4o-mini-transcribe",
    provider_attempts=6,
    billed_quantity=18_049,
    actual_microusd=38_660,
)

_PREEXISTING_RECEIPTS = {
    _CHERNIGOVSKAYA_RECEIPT.source_sha256: _CHERNIGOVSKAYA_RECEIPT,
}


def preexisting_receipt_for_source(
    source_sha256: str,
) -> PreexistingTranscriptionReceipt | None:
    """Return a proven historical receipt that must be imported, not rerun."""

    return _PREEXISTING_RECEIPTS.get(source_sha256)
