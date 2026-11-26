"""Project committed Watch state into the one existing private Mark issue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_mark_transcript import WatchMarkTranscript, watch_transcript_readback
from app.infrastructure.watch_voice_github import REPOSITORY, WatchVoiceError, marker
from app.models.watch_voice import WatchEarlyCard, WatchVoiceNote

EARLY_STATUS = "Статус: запись обнаружена; расшифровка ожидается."
UPDATE_RETRY = timedelta(seconds=60)
MAX_BACKFILL_EVENTS = 12
_STRICT_ERRORS = {
    "github_watch_issue_mismatch", "watch_mark_block_mismatch", "watch_mark_source_mismatch",
    "watch_mark_version_mismatch", "watch_mark_transcript_conflict",
    "watch_mark_transcript_invalid", "watch_mark_legacy_transcript_present",
    "watch_mark_pending_part_conflict",

}


@dataclass(frozen=True)
class MarkUpdate:
    event_id: str
    issue_number: int
    issue_url: str
    source_issue_url: str | None
    version: int
    state: str
    recognition_started_at: datetime | None
    text_saved_at: datetime | None
    legacy_finished_at: datetime | None
    transcript: str | None
    pending_part: int | None
    pending_digest: str | None

    @property
    def phase(self) -> str:
        if self.state == "processing":
            return "recognizing" if self.recognition_started_at else "starting"
        if self.state == "transcribed" and not self.transcript:
            return "unknown"
        return self.state

    @property
    def ready_at(self) -> datetime | None:
        return self.text_saved_at or self.legacy_finished_at


def _queue(scope: dict[str, str], *columns: Any) -> Any:
    return (
        select(*columns)
        .select_from(WatchVoiceNote)
        .join(WatchEarlyCard, and_(
            WatchEarlyCard.id == WatchVoiceNote.id,
            WatchEarlyCard.workspace_key == WatchVoiceNote.workspace_key,
            WatchEarlyCard.account_id == WatchVoiceNote.account_id,
            WatchEarlyCard.file_id == WatchVoiceNote.file_id,
            WatchEarlyCard.revision == WatchVoiceNote.revision,
        ))
        .where(
            WatchVoiceNote.workspace_key == scope["workspace_key"],
            WatchVoiceNote.account_id == scope["account_id"],
            WatchVoiceNote.webhook_archived.is_(True),
            WatchVoiceNote.mark_publish_version > 0,
            WatchEarlyCard.state == "saved",
            WatchEarlyCard.issue_number.is_not(None),
            WatchEarlyCard.issue_url.is_not(None),
            WatchVoiceNote.mark_publish_version > WatchEarlyCard.mark_published_version,
            WatchEarlyCard.mark_update_blocked.is_(False),
            or_(
                WatchEarlyCard.mark_update_retry_at.is_(None),
                WatchEarlyCard.mark_update_retry_at <= datetime.now(UTC),
            ),
        )
    )


async def has_mark_updates(session: AsyncSession, scope: dict[str, str]) -> bool:
    return await session.scalar(_queue(scope, WatchEarlyCard.id).limit(1)) is not None


_STATUS_TEXT = {
    "received": "оригинал сохранён в Gate, расшифровка в очереди.",
    "starting": "Gate начал обработку записи.",
    "recognizing": "идёт распознавание.",
    "transcribed": "расшифровка готова в Gate.",
    "rejected": "обработка завершилась отказом.",
    "unknown": "исход распознавания не подтверждён.",
}


def mark_block(job: MarkUpdate, transcript: WatchMarkTranscript | None) -> str:
    state = job.phase
    lines = [f"Статус: {_STATUS_TEXT[state]}", f"ID Gate: `{job.event_id}`."]
    if state == "transcribed" and job.ready_at is not None:
        ready_at = job.ready_at
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=UTC)
        lines.append(f"Текст сохранён в Gate: {ready_at.isoformat()}.")
    if transcript is not None:
        lines.append(transcript.label)
        if len(transcript.parts) == 1:
            lines.extend(("", transcript.part_body(1)))
        else:
            lines.append(
                f"Полная расшифровка в комментариях Gate, частей: {len(transcript.parts)}."
            )
    return (
        f"<!-- gate-watch-status:v1 event_id={job.event_id} -->\n"
        f"<!-- version={job.version} state={state} -->\n"
        + "\n".join(lines)
        + f"\n<!-- /gate-watch-status:v1 event_id={job.event_id} -->"
    )


def merged_issue_body(body: str, event_id: str, block: str, version: int) -> str:
    if body.split("\n").count(marker(event_id, 0)) != 1:
        raise WatchVoiceError("watch_mark_block_mismatch")
    opening = f"<!-- gate-watch-status:v1 event_id={event_id} -->"
    closing = f"<!-- /gate-watch-status:v1 event_id={event_id} -->"
    lines = body.split("\n")
    if any(line.startswith(("<!-- gate-watch-status:v1 ", "<!-- /gate-watch-status:v1 "))
           for line in lines):
        if lines.count(opening) != 1 or lines.count(closing) != 1:
            raise WatchVoiceError("watch_mark_block_mismatch")
        start, end = lines.index(opening), lines.index(closing)
        if start + 1 >= end:
            raise WatchVoiceError("watch_mark_block_mismatch")
        version_match = re.fullmatch(r"<!-- version=([1-9][0-9]{0,9}) state=[a-z]+ -->",
                                     lines[start + 1])
        if version_match is None or int(version_match[1]) > version:
            raise WatchVoiceError("watch_mark_version_mismatch")
        return "\n".join([*lines[:start], block, *lines[end + 1:]])
    before, separator, after = body.partition("\n" + EARLY_STATUS + "\n")
    if not separator or "\n" + EARLY_STATUS + "\n" in after:
        raise WatchVoiceError("watch_mark_block_mismatch")
    after = after.removeprefix(f"ID Gate: `{event_id}`.\n")
    return before + "\n" + block + "\n" + after


def backfill_event_ids(settings: Settings) -> tuple[str, ...]:
    configured = settings.gate_watch_mark_backfill_event_ids.get_secret_value().strip()
    if not configured:
        return ()
    try:
        values = tuple(str(UUID(value.strip())) for value in configured.split(","))
    except ValueError:
        return ()
    if not 1 <= len(values) <= MAX_BACKFILL_EVENTS or len(set(values)) != len(values):
        return ()
    return values


async def enroll_mark_backfill(
    settings: Settings, factory: async_sessionmaker[AsyncSession], scope: dict[str, str],
) -> None:
    """Enroll only the exact configured cohort. Repeating a launch is idempotent."""
    if not settings.gate_watch_mark_updates_enabled:
        return
    event_ids = backfill_event_ids(settings)
    if not event_ids:
        return
    async with factory.begin() as session:
        rows = (await session.execute(
            select(
                WatchVoiceNote.id, WatchVoiceNote.mark_publish_version,
                WatchVoiceNote.issue_url, WatchEarlyCard.issue_url, WatchEarlyCard.issue_number,
            ).join(WatchEarlyCard, and_(
                WatchEarlyCard.id == WatchVoiceNote.id,
                WatchEarlyCard.workspace_key == WatchVoiceNote.workspace_key,
                WatchEarlyCard.account_id == WatchVoiceNote.account_id,
                WatchEarlyCard.file_id == WatchVoiceNote.file_id,
                WatchEarlyCard.revision == WatchVoiceNote.revision,
            )).where(
                WatchVoiceNote.id.in_(event_ids),
                WatchVoiceNote.workspace_key == scope["workspace_key"],
                WatchVoiceNote.account_id == scope["account_id"],
                WatchVoiceNote.webhook_archived.is_(True),
                WatchVoiceNote.transcription_state == "transcribed",
                WatchVoiceNote.transcript.is_not(None),
                WatchVoiceNote.transcript != "",
                WatchEarlyCard.state == "saved",
                WatchEarlyCard.issue_number > 0,
                WatchEarlyCard.issue_url.is_not(None),
            )
        )).all()
        if len(rows) != len(event_ids) or any(
            card_url != f"https://github.com/{REPOSITORY}/issues/{number}"
            or (note_url is not None and note_url != card_url)
            for _, _, note_url, card_url, number in rows
        ) or len({row.issue_number for row in rows}) != len(event_ids):
            raise WatchVoiceError("watch_mark_backfill_scope_mismatch")
        await session.execute(
            update(WatchVoiceNote).where(
                WatchVoiceNote.id.in_([row.id for row in rows]),
                WatchVoiceNote.mark_publish_version == 0,
            ).values(mark_publish_version=1)
        )


async def _set_pending_part(
    factory: async_sessionmaker[AsyncSession], job: MarkUpdate, number: int | None,
    digest: str | None,
) -> None:
    async with factory.begin() as session:
        matched = await session.scalar(
            update(WatchEarlyCard).where(
                WatchEarlyCard.id == job.event_id,
                WatchEarlyCard.state == "saved",
                WatchEarlyCard.issue_number == job.issue_number,
                WatchEarlyCard.issue_url == job.issue_url,
                WatchEarlyCard.mark_published_version < job.version,
            ).values(mark_pending_part=number, mark_pending_digest=digest)
            .returning(WatchEarlyCard.id)
        )
        if matched is None:
            raise WatchVoiceError("watch_mark_source_mismatch")


def _verified_parts(
    job: MarkUpdate, transcript: WatchMarkTranscript, comments: list[str],
) -> set[int]:
    seen: set[int] = set()
    for comment in comments:
        lines = comment.split("\n")
        first = lines[0]
        if not first.startswith(f"<!-- gate-inbox:v1 event_id={job.event_id} part="):
            continue
        if len(lines) < 2 or not lines[1].startswith("<!-- gate-watch-transcript:v1 "):
            raise WatchVoiceError("watch_mark_legacy_transcript_present")
        part = re.fullmatch(
            r"<!-- gate-watch-transcript:v1 event_id=([a-f0-9-]{36}) "
            r"sha256=([a-f0-9]{64}) part=([1-9][0-9]?)/([1-9][0-9]?) -->",
            lines[1],
        )
        if part is None or part[1] != job.event_id or first != marker(job.event_id, int(part[3])):
            raise WatchVoiceError("watch_mark_transcript_conflict")
        if part[2] != transcript.digest:
            continue
        number = int(part[3])
        if (
            number > len(transcript.parts)
            or number in seen
            or comment != transcript.part_body(number)
        ):
            raise WatchVoiceError("watch_mark_transcript_conflict")
        seen.add(number)
    return seen


async def _prepare_transcript(
    factory: async_sessionmaker[AsyncSession],
    client: AppInboxClient,
    job: MarkUpdate,
    transcript: WatchMarkTranscript,
) -> list[str] | None:
    comments = await client.watch_issue_comments(job.issue_number)
    parts = _verified_parts(job, transcript, comments)
    if job.pending_part is not None:
        if job.pending_digest != transcript.digest:
            raise WatchVoiceError("watch_mark_pending_part_conflict")
        if job.pending_part not in parts:
            # Absence from a read cannot settle an uncertain comment POST.
            raise WatchVoiceError("watch_mark_pending_part_unconfirmed", unknown=True)
        await _set_pending_part(factory, job, None, None)
    if len(transcript.parts) == 1:
        if parts:
            raise WatchVoiceError("watch_mark_transcript_conflict")
        return comments

    number = next((i for i in range(1, len(transcript.parts) + 1) if i not in parts), None)
    if number is None:
        return comments
    await _set_pending_part(factory, job, number, transcript.digest)
    try:
        await client.post(
            part=number, issue_number=job.issue_number, title="", body=transcript.part_body(number)
        )
    except WatchVoiceError as exc:
        if not exc.unknown:
            await _set_pending_part(factory, job, None, None)
        raise
    comments = await client.watch_issue_comments(job.issue_number)
    parts = _verified_parts(job, transcript, comments)
    if number not in parts:
        raise WatchVoiceError("watch_mark_pending_part_unconfirmed", unknown=True)
    await _set_pending_part(factory, job, None, None)
    return comments if len(parts) == len(transcript.parts) else None


async def publish_mark_update(
    factory: async_sessionmaker[AsyncSession],
    client: AppInboxClient,
    scope: dict[str, str],
) -> bool:
    async with factory() as session:
        row = (await session.execute(_queue(
            scope, WatchEarlyCard.id, WatchEarlyCard.issue_number, WatchEarlyCard.issue_url,
            WatchVoiceNote.issue_url, WatchVoiceNote.mark_publish_version,
            WatchVoiceNote.transcription_state, WatchVoiceNote.recognition_started_at,
            WatchVoiceNote.text_saved_at, WatchVoiceNote.transcribed_at, WatchVoiceNote.transcript,
            WatchEarlyCard.mark_pending_part, WatchEarlyCard.mark_pending_digest,
        ).order_by(WatchEarlyCard.detected_at, WatchEarlyCard.id).limit(1))).one_or_none()
    if row is None:
        return False
    job = MarkUpdate(*row)
    body_path = f"https://github.com/{REPOSITORY}/issues/{job.issue_number}"
    changes: dict[str, object]
    try:
        if job.issue_number <= 0 or job.issue_url != body_path or (
            job.source_issue_url and job.source_issue_url != body_path
        ):
            raise WatchVoiceError("watch_mark_source_mismatch")
        body = await client.watch_issue_body(job.issue_number, job.event_id)
        transcript = None
        comments: list[str] = []
        if job.phase == "transcribed" and job.transcript is not None:
            transcript = WatchMarkTranscript.from_text(job.event_id, job.transcript)
            # Validate this issue before preparing any comments.
            merged_issue_body(body, job.event_id, mark_block(job, None), job.version)
            prepared = await _prepare_transcript(factory, client, job, transcript)
            if prepared is None:
                return True
            comments = prepared
        elif job.pending_part is not None:
            raise WatchVoiceError("watch_mark_pending_part_conflict")
        expected = merged_issue_body(
            body, job.event_id, mark_block(job, transcript), job.version,
        )
        if expected != body:
            await client.update_watch_issue(job.issue_number, expected)
            verified = await client.watch_issue_body(job.issue_number, job.event_id)
            if verified != expected:
                raise WatchVoiceError("github_watch_issue_readback_mismatch")
        if transcript is not None:
            verified_text = watch_transcript_readback(job.event_id, expected, comments)
            if verified_text != job.transcript:
                raise WatchVoiceError("watch_mark_transcript_readback_failed")
        changes = {
            "mark_published_version": job.version,
            "mark_published_at": datetime.now(UTC),
            "mark_update_error": None,
            "mark_update_retry_at": None,
        }
    except WatchVoiceError as exc:
        changes = {
            "mark_update_error": exc.code,
            "mark_update_blocked": exc.code in _STRICT_ERRORS,
            "mark_update_retry_at": datetime.now(UTC) + UPDATE_RETRY,
        }

    async with factory.begin() as session:
        await session.execute(
            update(WatchEarlyCard).where(
                WatchEarlyCard.id == job.event_id,
                WatchEarlyCard.issue_number == job.issue_number,
                WatchEarlyCard.issue_url == job.issue_url,
                WatchEarlyCard.mark_published_version < job.version,
            ).values(**changes)
        )
    return True
