"""Project committed Watch state into the one existing private Mark issue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.watch_browser import watch_browser, watch_path
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_github import REPOSITORY, WatchVoiceError, marker
from app.models.watch_voice import WatchEarlyCard, WatchVoiceNote

EARLY_STATUS = "Статус: запись обнаружена; расшифровка ожидается."
UPDATE_RETRY = timedelta(seconds=60)
_STRICT_ERRORS = {
    "github_watch_issue_mismatch", "watch_mark_block_mismatch", "watch_mark_source_mismatch",
    "watch_mark_version_mismatch",
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


def mark_block(settings: Settings, job: MarkUpdate) -> str:
    state = job.phase
    lines = [f"Статус: {_STATUS_TEXT[state]}"]
    if state == "transcribed" and job.ready_at is not None:
        ready_at = job.ready_at
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=UTC)
        lines.append(f"Текст сохранён в Gate: {ready_at.isoformat()}.")
    if config := watch_browser(settings):
        link = config.origin + watch_path(job.event_id)
        link_line = f"ID Gate: `{job.event_id}`. [Открыть запись в Gate]({link})."
        lines.append(link_line)
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
    for line in block.split("\n"):
        if line.startswith("ID Gate: ") and after.startswith(line + "\n"):
            after = after.removeprefix(line + "\n")
    return before + "\n" + block + "\n" + after


async def publish_mark_update(
    settings: Settings,
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
        expected = merged_issue_body(body, job.event_id, mark_block(settings, job), job.version)
        if expected != body:
            await client.update_watch_issue(job.issue_number, expected)
            verified = await client.watch_issue_body(job.issue_number, job.event_id)
            if verified != expected:
                raise WatchVoiceError("github_watch_issue_readback_mismatch")
        changes = {
            "mark_published_version": job.version,
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
