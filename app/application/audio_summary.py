"""Deterministic, content-preserving summaries for stored audio transcripts."""

from __future__ import annotations

import re

AUDIO_SUMMARY_MODEL = "extractive-ru-v1"
_MAX_SUMMARY_CHARS = 600
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\\s+")


def build_audio_summary(transcript: str) -> str:
    """Return a bounded Russian-readable summary without another provider call."""

    normalized = " ".join(transcript.split())
    if not normalized:
        raise ValueError("audio_summary_transcript_empty")
    if len(normalized) <= _MAX_SUMMARY_CHARS:
        return normalized

    sentences = [item.strip() for item in _SENTENCE_BOUNDARY.split(normalized) if item.strip()]
    if len(sentences) <= 2:
        return normalized[: _MAX_SUMMARY_CHARS - 1].rstrip() + "…"

    candidate = " ".join((sentences[0], sentences[1], sentences[-1]))
    if len(candidate) <= _MAX_SUMMARY_CHARS:
        return candidate
    return candidate[: _MAX_SUMMARY_CHARS - 1].rstrip() + "…"
