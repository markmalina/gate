"""One early private Inbox issue per source, independent of archive and STT.

Every POST consumes a durable claim. Unknown/inflight outcomes are reconciled
by exact frozen body and writer; absence never permits another create request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_voice_github import (
    GithubReceipt,
    MarkInboxClient,
    WatchVoiceError,
    body_digest,
    marker,
)
from app.infrastructure.watch_voice_journal import pulse
from app.infrastructure.watch_voice_sources import source_scope
from app.models.watch_voice import WatchVoiceNote, WatchVoiceSource


def card_body(source: WatchVoiceSource) -> str:
    status = (
        "Аудио получено, обработка заблокирована."
        if source.state == "rejected"
        else "Аудио получено, расшифровка ожидается."
    )
    return (
        f"{marker(source.id, 0)}\n\nАвтор: Марк Малиновский. Доставщик: Gate.\n"
        f"Состояние на момент получения: {status}\n"
        "Получение аудио, расшифровка и исполнение поручений — отдельные состояния.\n\n"
        f"Gate receipt / event_id: `{source.id}`\n"
        f"Dropbox source: `{source.file_id}`, revision `{source.revision}`.\n"
        f"Файл: `{source.filename}`. Размер: {source.size_bytes} bytes.\n"
        f"Обнаружено Gate: {source.detected_at.isoformat()}.\n"
        "Время начала записи: не подтверждено.\n"
        "Оригинал остаётся в приватном Dropbox; публичная ссылка не создаётся.\n"
        f"Защищённый архив Gate: `watch-voice-file:{source.id}` "
        "(доступен после сохранения оригинала, независимо от расшифровки).\n"
        f"Состояние архива при обнаружении: {source.state}.\n"
        f"Ошибка при обнаружении: {source.last_error or 'нет'}.\n\n"
        "Полная расшифровка появится в этой же issue отдельными частями.\n"
        "Статус поручений: не начато.\n"
    )


async def save_card_receipt(
    factory: async_sessionmaker[AsyncSession],
    source: WatchVoiceSource,
    receipt: GithubReceipt,
) -> bool:
    async with factory() as session:
        changed = await session.execute(
            update(WatchVoiceSource)
            .where(
                WatchVoiceSource.id == source.id,
                WatchVoiceSource.workspace_key == source.workspace_key,
                WatchVoiceSource.account_id == source.account_id,
                WatchVoiceSource.card_claim_id == source.card_claim_id,
                WatchVoiceSource.card_body_sha256 == source.card_body_sha256,
                WatchVoiceSource.card_state.in_(("sending", "unknown")),
            )
            .values(card_state="saved", card_issue_number=receipt.number,
                    card_issue_url=receipt.url, card_received_at=receipt.received_at,
                    card_error=None)
        )
        await session.commit()
        current = await session.get(WatchVoiceSource, source.id)
        if current is None or current.card_state != "saved":
            return False
        if (current.card_issue_number, current.card_issue_url) != (receipt.number, receipt.url):
            raise WatchVoiceError("watch_voice_card_receipt_conflict")
        note = await session.get(WatchVoiceNote, source.id)
        saved = bool(changed.rowcount)  # type: ignore[attr-defined]
    if note is not None:
        await transcript_card_offset(factory, note)
    return saved


async def publish_source_card(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    client: MarkInboxClient,
    key: str,
) -> bool:
    if not settings.gate_watch_voice_early_inbox_enabled:
        return False
    async with factory() as session:
        row = await session.scalar(
            select(WatchVoiceSource).where(WatchVoiceSource.id == key, *source_scope(settings))
        )
        if row is None or row.card_state in {"legacy", "saved", "blocked"}:
            return False
        if row.card_state == "pending" and row.card_body is None:
            body = card_body(row)
            await session.execute(
                update(WatchVoiceSource)
                .where(WatchVoiceSource.id == key, WatchVoiceSource.card_state == "pending",
                       WatchVoiceSource.card_body.is_(None))
                .values(card_body=body, card_body_sha256=body_digest(body),
                        card_title=f"[Gate][Voice] Входящая запись · {row.id[:8]}")
            )
            await session.commit()
            await session.refresh(row)
        if not row.card_body or row.card_body_sha256 != body_digest(row.card_body):
            raise WatchVoiceError("watch_voice_card_payload_conflict")
        if row.card_state == "pending":
            claim = str(uuid.uuid4())
            claimed = await session.execute(
                update(WatchVoiceSource)
                .where(WatchVoiceSource.id == key, WatchVoiceSource.card_state == "pending",
                       WatchVoiceSource.card_body_sha256 == row.card_body_sha256)
                .values(card_state="sending", card_claim_id=claim, card_writer=client.writer,
                        card_attempted_at=datetime.now(UTC))
            )
            await session.commit()
            if not claimed.rowcount:  # type: ignore[attr-defined]
                return False
            row.card_state, row.card_claim_id, row.card_writer = "sending", claim, client.writer
            create = True
        else:
            if not row.card_writer:
                raise WatchVoiceError("watch_voice_card_writer_missing")
            # An inflight publisher may still finish. Moving to unknown never
            # authorizes POST; its fenced exact receipt can still be accepted.
            if row.card_state == "sending":
                await session.execute(
                    update(WatchVoiceSource)
                    .where(WatchVoiceSource.id == key, WatchVoiceSource.card_state == "sending",
                           WatchVoiceSource.card_claim_id == row.card_claim_id)
                    .values(card_state="unknown", card_error="watch_voice_card_inflight_unknown")
                )
                await session.commit()
                row.card_state = "unknown"
            create = False
    try:
        await pulse("early_card_publication", seconds=180)
        receipt: GithubReceipt | None
        if create:
            receipt = await client.post(part=0, issue_number=None,
                                        title=row.card_title or "[Gate][Voice] Входящая запись",
                                        body=row.card_body)
        else:
            receipt = await client.reconcile(part=0, issue_number=None, body=row.card_body,
                                             expected_writer=row.card_writer)
            if receipt is None:
                return False
    except WatchVoiceError as exc:
        async with factory() as session:
            await session.execute(
                update(WatchVoiceSource)
                .where(WatchVoiceSource.id == key,
                       WatchVoiceSource.card_claim_id == row.card_claim_id,
                       WatchVoiceSource.card_state.in_(("sending", "unknown")))
                .values(card_state="unknown" if exc.unknown or not create else "blocked",
                        card_error=exc.code)
            )
            await session.commit()
        return False
    return await save_card_receipt(factory, row, receipt)


async def drain_cards(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    client: MarkInboxClient,
    cutover: datetime | None = None,
) -> int:
    if not settings.gate_watch_voice_early_inbox_enabled:
        return 0
    async with factory() as session:
        keys = list((await session.scalars(
            select(WatchVoiceSource.id)
            .where(*source_scope(settings, cutover),
                   WatchVoiceSource.card_state.in_(("pending", "sending", "unknown")))
            .order_by(case((WatchVoiceSource.card_state == "pending", 0), else_=1),
                      WatchVoiceSource.detected_at, WatchVoiceSource.id).limit(50)
        )).all())
    return sum([await publish_source_card(settings, factory, client, key) for key in keys])


async def transcript_card_offset(
    factory: async_sessionmaker[AsyncSession], note: WatchVoiceNote,
) -> int | None:
    """Bind the existing early card before any text POST; preserve legacy parts."""
    async with factory() as session:
        source = await session.get(WatchVoiceSource, note.id)
        if source is None or source.card_state == "legacy":
            return 0
        if (source.workspace_key, source.account_id, source.file_id, source.revision) != (
            note.workspace_key, note.account_id, note.file_id, note.revision
        ):
            raise WatchVoiceError("watch_voice_card_scope_conflict")
        if source.card_state != "saved":
            return None  # Unknown create must never fall back to a second issue.
        if source.card_issue_number is None or source.card_issue_url is None:
            raise WatchVoiceError("watch_voice_card_receipt_missing")
        if note.issue_number is None:
            await session.execute(
                update(WatchVoiceNote)
                .where(WatchVoiceNote.id == note.id, WatchVoiceNote.issue_number.is_(None),
                       WatchVoiceNote.next_part == 0, WatchVoiceNote.delivery_state == "pending")
                .values(issue_number=source.card_issue_number, issue_url=source.card_issue_url)
            )
            await session.commit()
            current = await session.get(WatchVoiceNote, note.id)
            if current is None:
                return None
            note.issue_number, note.issue_url = current.issue_number, current.issue_url
            note.github_received_at = current.github_received_at
        if (note.issue_number, note.issue_url) != (source.card_issue_number, source.card_issue_url):
            raise WatchVoiceError("watch_voice_card_receipt_conflict")
    return 1


async def card_status(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    rows = await session.execute(
        select(WatchVoiceSource.card_state, func.count())
        .where(*source_scope(settings)).group_by(WatchVoiceSource.card_state)
    )
    counts = {str(state): count for state, count in rows}
    errors = await session.execute(
        select(WatchVoiceSource.card_error, func.count())
        .where(*source_scope(settings), WatchVoiceSource.card_error.is_not(None))
        .group_by(WatchVoiceSource.card_error)
    )
    # Only aggregates leave the scoped journal, never source names or issue bodies.
    timings = await session.execute(
        select(WatchVoiceSource.detected_at, WatchVoiceSource.card_received_at)
        .where(*source_scope(settings), WatchVoiceSource.card_state == "saved")
    )
    seconds = []
    for detected, received in timings:
        if detected is not None and received is not None:
            start = detected.replace(tzinfo=UTC) if detected.tzinfo is None else detected
            finish = received.replace(tzinfo=UTC) if received.tzinfo is None else received
            if finish >= start:
                seconds.append((finish - start).total_seconds())
    return {
        "enabled": settings.gate_watch_voice_early_inbox_enabled,
        "counts": counts,
        "error_counts": {str(code): count for code, count in errors},
        "detection_to_card_seconds": {
            "samples": len(seconds),
            "min": min(seconds) if seconds else None,
            "max": max(seconds) if seconds else None,
        },
        "private_content_exposed": False,
    }
