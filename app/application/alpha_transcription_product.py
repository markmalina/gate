"""Pure contracts and deterministic assembly for Alfa transcription."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

SUPPORTED_MEDIA_SUFFIXES = {
    ".3gp", ".aac", ".amr", ".flac", ".m4a", ".mp3", ".mp4", ".mpeg",
    ".mpga", ".oga", ".ogg", ".opus", ".wav", ".webm",
}
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_DURATION_MILLIS = 24 * 60 * 60 * 1000
MAX_PARTS = 512
CHUNK_MILLIS = 230_000
CHUNK_STRIDE_MILLIS = 225_000


class AlphaTranscriptionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PartWindow:
    index: int
    start_millis: int
    end_millis: int


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CreateAlphaTranscriptionCommand:
    workspace_id: str
    source_path: str
    source_filename: str
    source_media_type: str
    title: str
    analysis_requested: bool = False
    language: str = "ru"
    model: str = "gpt-4o-mini-transcribe"

    def __post_init__(self) -> None:
        validate_private_path(self.source_path)
        validate_source_filename(self.source_filename)
        if not self.source_media_type or len(self.source_media_type) > 128:
            raise AlphaTranscriptionError("alpha_transcription_media_type_invalid")
        if not self.title.strip() or len(self.title.strip()) > 255:
            raise AlphaTranscriptionError("alpha_transcription_title_invalid")
        if not self.language.strip() or len(self.language) > 32:
            raise AlphaTranscriptionError("alpha_transcription_language_invalid")
        if self.model not in {"gpt-4o-mini-transcribe", "gpt-4o-transcribe"}:
            raise AlphaTranscriptionError("alpha_transcription_model_invalid")


def validate_private_path(path: str) -> str:
    if not path or path != path.strip() or len(path) > 1024 or not path.startswith("/"):
        raise AlphaTranscriptionError("alpha_transcription_source_path_invalid")
    parsed = PurePosixPath(path)
    if any(part in {"", ".", ".."} for part in parsed.parts[1:]):
        raise AlphaTranscriptionError("alpha_transcription_source_path_invalid")
    return parsed.as_posix()


def validate_source_filename(filename: str) -> str:
    if not filename or filename != filename.strip() or len(filename) > 255:
        raise AlphaTranscriptionError("alpha_transcription_filename_invalid")
    if filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise AlphaTranscriptionError("alpha_transcription_filename_invalid")
    suffix = PurePosixPath(filename).suffix.casefold()
    if suffix not in SUPPORTED_MEDIA_SUFFIXES:
        raise AlphaTranscriptionError("alpha_transcription_format_unsupported")
    return filename


def safe_title(value: str, *, fallback: str) -> str:
    title = re.sub(r"\s+", " ", value).strip(" .")
    if not title:
        title = PurePosixPath(fallback).stem or "Запись"
    return title[:255]


def build_part_windows(duration_millis: int) -> tuple[PartWindow, ...]:
    if duration_millis <= 0 or duration_millis > MAX_DURATION_MILLIS:
        raise AlphaTranscriptionError("alpha_transcription_duration_invalid")
    windows: list[PartWindow] = []
    start = 0
    while start < duration_millis:
        end = min(duration_millis, start + CHUNK_MILLIS)
        windows.append(PartWindow(len(windows), start, end))
        if end == duration_millis:
            break
        start += CHUNK_STRIDE_MILLIS
    if not windows or len(windows) > MAX_PARTS:
        raise AlphaTranscriptionError("alpha_transcription_too_many_parts")
    if windows[-1].end_millis != duration_millis:
        raise AlphaTranscriptionError("alpha_transcription_coverage_incomplete")
    return tuple(windows)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def merge_overlapping_transcripts(parts: list[str]) -> str:
    """Merge overlapping ASR parts using conservative word-boundary matching."""

    normalized = [_normalize_verbatim(part) for part in parts if part.strip()]
    if not normalized:
        raise AlphaTranscriptionError("alpha_transcription_transcript_empty")
    merged = normalized[0]
    for current in normalized[1:]:
        previous_words = merged.split()
        current_words = current.split()
        max_overlap = min(80, len(previous_words), len(current_words))
        overlap = 0
        for size in range(max_overlap, 3, -1):
            left = [_comparison_word(word) for word in previous_words[-size:]]
            right = [_comparison_word(word) for word in current_words[:size]]
            if left == right and any(left):
                overlap = size
                break
        remainder = " ".join(current_words[overlap:]).strip()
        if remainder:
            merged = f"{merged.rstrip()}\n\n{remainder}"
    return merged.strip()


def clean_transcript(verbatim: str) -> str:
    """Produce a conservative readable edition without changing propositions."""

    text = _normalize_verbatim(verbatim)
    text = re.sub(r"\b([А-Яа-яA-Za-z]{2,})(?:\s+\1){2,}\b", r"\1", text, flags=re.I)
    text = re.sub(r"(?:\s*\[(?:шум|неразборчиво|тишина)\]\s*){2,}", " [неразборчиво] ", text, flags=re.I)
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n{2,}", text)]
    return "\n\n".join(item for item in paragraphs if item).strip()


def analyze_transcript(verbatim: str) -> str:
    """Extract a conservative evidence-linked analysis without a second paid call."""

    text = _normalize_verbatim(verbatim)
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?…])\s+|\n+", text)
        if len(item.strip()) >= 12
    ]
    speaker_names = sorted(
        {
            match.group(1).strip()
            for match in re.finditer(
                r"(?m)^([А-ЯЁA-Z][А-Яа-яЁёA-Za-z .'-]{1,48}):\s+",
                text,
            )
        }
    )
    themes = _top_terms(text, maximum=8)
    agreements = _matching_sentences(
        sentences,
        ("договор", "решили", "соглас", "условились", "подтверд"),
        maximum=8,
    )
    tasks = _matching_sentences(
        sentences,
        ("нужно", "надо", "сделать", "подготов", "отправ", "провер", "задач"),
        maximum=12,
    )
    facts = _salient_sentences(sentences, maximum=10)
    return (
        "# Анализ записи\n\n"
        "## Говорящие\n\n"
        + (_markdown_list(speaker_names) if speaker_names else "Автоматически не подтверждены.\n")
        + "\n## Темы\n\n"
        + (_markdown_list(themes) if themes else "Не выделены.\n")
        + "\n## Договорённости\n\n"
        + (_markdown_list(agreements) if agreements else "Явные договорённости не обнаружены.\n")
        + "\n## Задачи\n\n"
        + (_markdown_list(tasks) if tasks else "Явные задачи не обнаружены.\n")
        + "\n## Важные факты\n\n"
        + (_markdown_list(facts) if facts else "Не выделены.\n")
        + "\n> Анализ извлечён только из текста записи и не заменяет дословную версию.\n"
    )


def _top_terms(text: str, *, maximum: int) -> list[str]:
    stop = {
        "который", "которая", "которые", "этого", "этот", "такая", "такой",
        "потому", "просто", "вообще", "очень", "можно", "нужно", "надо",
        "будет", "были", "было", "есть", "если", "когда", "чтобы", "тогда",
        "только", "тоже", "сейчас", "здесь", "себя", "свою", "свои", "того",
        "what", "that", "this", "with", "from", "have", "will", "there",
    }
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for raw in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text):
        key = raw.casefold()
        if key in stop:
            continue
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, raw)
    ranked = sorted(counts, key=lambda item: (-counts[item], item))
    return [f"{display[item]} — {counts[item]} упоминаний" for item in ranked[:maximum] if counts[item] >= 2]


def _matching_sentences(
    sentences: list[str], markers: tuple[str, ...], *, maximum: int
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        folded = sentence.casefold()
        if not any(marker in folded for marker in markers):
            continue
        key = re.sub(r"\W+", "", folded)
        if key in seen:
            continue
        seen.add(key)
        result.append(sentence[:500])
        if len(result) >= maximum:
            break
    return result


def _salient_sentences(sentences: list[str], *, maximum: int) -> list[str]:
    ranked = sorted(
        enumerate(sentences),
        key=lambda item: (-min(len(item[1]), 240), item[0]),
    )
    selected = sorted(ranked[:maximum], key=lambda item: item[0])
    return [sentence[:500] for _, sentence in selected]


def _markdown_list(items: list[str]) -> str:
    return "".join(f"- {item}\n" for item in items)


def build_manifest(
    *,
    job_id: str,
    title: str,
    source_filename: str,
    source_path: str,
    source_sha256: str,
    source_size_bytes: int,
    duration_millis: int,
    model: str,
    language: str,
    chunk_count: int,
    billed_quantity: int,
    actual_microusd: int,
    artifacts: dict[str, StoredArtifact],
    created_at: datetime,
    completed_at: datetime,
) -> bytes:
    payload: dict[str, Any] = {
        "schema": "gate.alpha-transcription.manifest@1",
        "job_id": job_id,
        "title": title,
        "source": {
            "filename": source_filename,
            "private_path": source_path,
            "sha256": source_sha256,
            "size_bytes": source_size_bytes,
            "duration_millis": duration_millis,
        },
        "provider": {
            "model": model,
            "language": language,
            "chunk_count": chunk_count,
            "billed_quantity": billed_quantity,
            "actual_microusd": actual_microusd,
        },
        "artifacts": {
            name: {
                "private_path": artifact.path,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
            }
            for name, artifact in sorted(artifacts.items())
        },
        "created_at": _iso(created_at),
        "completed_at": _iso(completed_at),
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _normalize_verbatim(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value


def _comparison_word(value: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "", value).casefold()


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
