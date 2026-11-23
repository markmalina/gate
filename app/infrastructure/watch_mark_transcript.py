"""Exact transcript format for the private Mark issue and ordered comments."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.infrastructure.watch_voice_github import (
    WatchVoiceError,
    body_digest,
    marker,
    transcript_parts,
)


@dataclass(frozen=True)
class WatchMarkTranscript:
    event_id: str
    digest: str
    parts: list[str]

    @classmethod
    def from_text(cls, event_id: str, text: str) -> WatchMarkTranscript:
        if not text or len(text) > 100_000:
            raise WatchVoiceError("watch_mark_transcript_invalid")
        return cls(
            event_id, body_digest(text),
            transcript_parts(text, byte_limit=32_000, line_prefix_bytes=4),
        )

    @property
    def label(self) -> str:
        mode = "inline" if len(self.parts) == 1 else "comments"
        return f"<!-- gate-watch-text:v1 sha256={self.digest} parts={len(self.parts)} {mode} -->"

    def part_body(self, number: int) -> str:
        part = self.parts[number - 1]
        quoted = "\n".join("    " + line for line in part.split("\n"))
        heading = f"Расшифровка Gate, часть {number} из {len(self.parts)}."
        return (
            f"{marker(self.event_id, number)}\n{self.part_tag(number)}\n{heading}\n\n"
            f"{quoted}\n\n<!-- /gate-watch-transcript:v1 -->"
        )

    def part_tag(self, number: int) -> str:
        return (
            f"<!-- gate-watch-transcript:v1 event_id={self.event_id} sha256={self.digest} "
            f"part={number}/{len(self.parts)} -->"
        )


def watch_transcript_readback(event_id: str, body: str, comments: Sequence[str]) -> str:
    """Verify the transcript a Work sees when it rereads raw Markdown from GitHub."""
    lines = body.split("\n")
    opening = f"<!-- gate-watch-status:v1 event_id={event_id} -->"
    closing = f"<!-- /gate-watch-status:v1 event_id={event_id} -->"
    if lines.count(opening) != 1 or lines.count(closing) != 1:
        raise WatchVoiceError("watch_mark_transcript_readback_failed")
    start, end = lines.index(opening), lines.index(closing)
    text_labels = [
        line for line in lines[start + 1:end] if line.startswith("<!-- gate-watch-text:")
    ]
    if len(text_labels) != 1:
        raise WatchVoiceError("watch_mark_transcript_readback_failed")
    label = re.fullmatch(
        r"<!-- gate-watch-text:v1 sha256=([a-f0-9]{64}) parts=([1-9][0-9]?) (inline|comments) -->",
        text_labels[0],
    )
    if not label or end <= start or "state=transcribed -->" not in lines[start + 1]:
        raise WatchVoiceError("watch_mark_transcript_readback_failed")
    digest, size, mode = label.groups()
    count = int(size)
    if not 1 <= count <= 20 or (mode == "inline") != (count == 1):
        raise WatchVoiceError("watch_mark_transcript_readback_failed")
    candidates = ["\n".join(lines[start + 1:end])] if mode == "inline" else comments
    parts: list[str] = []
    for number in range(1, count + 1):
        tag = (
            f"<!-- gate-watch-transcript:v1 event_id={event_id} sha256={digest} "
            f"part={number}/{count} -->"
        )
        matches = [block.split("\n") for block in candidates if tag in block.split("\n")]
        if len(matches) != 1 or matches[0].count(tag) != 1:
            raise WatchVoiceError("watch_mark_transcript_readback_failed")
        block = matches[0]
        offset = block.index(tag)
        tail = block[offset + 3:]
        try:
            end_part = tail.index("<!-- /gate-watch-transcript:v1 -->")
        except ValueError:
            raise WatchVoiceError("watch_mark_transcript_readback_failed") from None
        quoted = tail[:end_part - 1]
        if (
            offset < 1
            or block[offset - 1] != marker(event_id, number)
            or end_part < 2
            or tail[end_part - 1] != ""
            or any(not line.startswith("    ") for line in quoted)
        ):
            raise WatchVoiceError("watch_mark_transcript_readback_failed")
        parts.append("\n".join(line[4:] for line in quoted))
    text = "".join(parts)
    if not text or body_digest(text) != digest:
        raise WatchVoiceError("watch_mark_transcript_readback_failed")
    return text
