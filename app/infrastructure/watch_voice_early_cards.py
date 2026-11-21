"""Default-off metadata-only Watch inbox. Never downloads or transcribes audio."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_voice_github import MarkInboxClient, WatchVoiceError, marker
from app.infrastructure.watch_voice_recovery import _utc
from app.models.watch_voice import (
    WatchEarlyCard,
    WatchEarlyCursor,
    WatchEarlyRejection,
    WatchVoiceNote,
)
from app.worker.dropbox_calls import (
    DropboxCallWorkerError,
    _refresh_access_token,
    _verify_account_id,
)
from app.worker.watch_voice import PATH, timestamp

_EXTENSIONS = {".m4a", ".mp4", ".mp3", ".wav", ".ogg", ".webm", ".flac"}


def after_cutover(modified: datetime, cutover: datetime) -> bool:
    # Dropbox reports whole seconds. Include the boundary second, since a file
    # arriving later within that second is indistinguishable from one before it.
    return _utc(modified) >= _utc(cutover).replace(microsecond=0)


def metadata(entry: object) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        raise WatchVoiceError("watch_early_entry_unclassified")
    if entry.get(".tag") in ("folder", "deleted"):
        return None
    if entry.get(".tag") != "file":
        raise WatchVoiceError("watch_early_entry_unclassified")
    path, name = entry.get("path_display"), entry.get("name")
    if not isinstance(path, str) or not path or not isinstance(name, str) or not name:
        raise WatchVoiceError("watch_early_name_or_path_missing")
    if not path.casefold().startswith(PATH.casefold() + "/"):
        return None
    if PurePosixPath(name).suffix.lower() not in _EXTENSIONS:
        return None
    if len(path) > 1024 or len(name) > 255:
        raise WatchVoiceError("watch_early_name_invalid")
    if any(
        not isinstance(entry.get(k), str) or not 0 < len(entry[k]) <= 255
        for k in ("id", "rev", "server_modified")
    ):
        raise WatchVoiceError("watch_early_metadata_invalid")
    if not re.fullmatch(r"id:[A-Za-z0-9_-]{1,252}", entry["id"]) or not re.fullmatch(
        r"[a-f0-9]{1,255}", entry["rev"]
    ):
        raise WatchVoiceError("watch_early_metadata_invalid")
    if type(entry.get("size")) is not int or entry["size"] <= 0:
        raise WatchVoiceError("watch_early_size_invalid")
    try:
        timestamp(entry["server_modified"])
    except (OverflowError, ValueError):
        raise WatchVoiceError("watch_early_time_invalid") from None
    return entry


def card_values(settings: Settings, item: dict[str, Any], detected: datetime) -> dict[str, Any]:
    source = (settings.gate_workspace_id, settings.dropbox_call_account_id, item["id"], item["rev"])
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, repr(source)))
    modified = timestamp(item["server_modified"])
    body = (
        f"{marker(event_id, 0)}\n\nАвтор: Марк Малиновский. Доставщик: Gate.\n"
        "Канал: Galaxy Watch → Samsung Звукозапись → Dropbox Voice.\n"
        "Статус: запись обнаружена; расшифровка ожидается.\n"
        f"Dropbox source: `{item['id']}`, revision `{item['rev']}`.\n"
        f"Изменён в Dropbox: {modified.isoformat()}.\n"
        f"Обнаружен Gate: {detected.isoformat()}.\n"
        "Оригинал остаётся в приватном Dropbox. Публичная ссылка не создаётся.\n"
        "Получение записи и выполнение поручений — отдельные состояния.\n"
    )
    return {
        "id": event_id,
        "workspace_key": source[0],
        "account_id": source[1],
        "file_id": source[2],
        "revision": source[3],
        "detected_at": detected,
        "source_modified_at": modified,
        "body": body,
        "title": f"[Gate][Voice] Входящая запись · {event_id[:8]}",
        "state": "pending",
    }


async def run_early_cards(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    client: MarkInboxClient,
) -> dict[str, Any]:
    key = watch_scope(settings)
    limit = getattr(settings, "gate_watch_voice_early_batch_size", 1)
    if type(limit) is not int or not 1 <= limit <= 5:
        raise WatchVoiceError("watch_early_batch_limit_invalid")
    await ensure_early_cursor(factory, key)
    discovery_error: WatchVoiceError | None = None
    try:
        await discover_early_cards(settings, factory, http, writer="legacy_actions")
    except WatchVoiceError as exc:
        discovery_error = exc
    receipt = await drain_saved(factory, client, key, limit)
    if discovery_error is not None and receipt["result"] == "idle":
        raise discovery_error
    return receipt


def watch_scope(settings: Settings) -> dict[str, str]:
    return {
        "workspace_key": settings.gate_workspace_id,
        "account_id": settings.dropbox_call_account_id,
    }


async def ensure_early_cursor(
    factory: async_sessionmaker[AsyncSession], key: dict[str, str]
) -> None:
    async with factory() as session:
        created = await session.execute(
            insert(WatchEarlyCursor)
            .values(**key, cutover_at=datetime.now(UTC))
            .on_conflict_do_nothing()
        )
        if created.rowcount:  # type: ignore[attr-defined]
            existing = await session.scalar(
                select(WatchEarlyCard.id)
                .where(
                    WatchEarlyCard.workspace_key == key["workspace_key"],
                    WatchEarlyCard.account_id == key["account_id"],
                )
                .limit(1)
            )
            if existing is None:
                existing = await session.scalar(
                    select(WatchEarlyRejection.metadata_sha256)
                    .where(
                        WatchEarlyRejection.workspace_key == key["workspace_key"],
                        WatchEarlyRejection.account_id == key["account_id"],
                    )
                    .limit(1)
                )
            if existing is not None:
                raise WatchVoiceError("watch_early_cursor_missing_with_cards")
        await session.commit()


async def discover_early_cards(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    *,
    writer: str,
    wake_received_at: datetime | None = None,
) -> bool:
    """Commit each provider page and its cursor together, with no lock during HTTP."""
    key = watch_scope(settings)
    async with factory() as session:
        cursor = await session.get(WatchEarlyCursor, (key["workspace_key"], key["account_id"]))
        if cursor is None or cursor.writer_mode != writer:
            raise WatchVoiceError("watch_early_writer_inactive")
        page_cursor, generation, cutover_at = (
            cursor.cursor,
            cursor.writer_generation,
            cursor.cutover_at,
        )
    try:
        async with asyncio.timeout(55):
            return await _discover_pages(
                settings, factory, http, writer, page_cursor, generation, cutover_at,
                wake_received_at,
            )
    except (TimeoutError, httpx.HTTPError, DropboxCallWorkerError):
        raise WatchVoiceError("watch_early_delta_unavailable") from None


async def _discover_pages(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    writer: str,
    page_cursor: str | None,
    generation: int,
    cutover_at: datetime,
    wake_received_at: datetime | None = None,
) -> bool:
    token = await _refresh_access_token(settings, http)
    await _verify_account_id(settings, http, token)
    key = watch_scope(settings)
    for _ in range(4):
        endpoint = "list_folder/continue" if page_cursor else "list_folder"
        body = (
            {"cursor": page_cursor}
            if page_cursor
            else {"path": PATH, "recursive": False, "include_deleted": False}
        )
        response = await http.post(
            "https://api.dropboxapi.com/2/files/" + endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            follow_redirects=False,
            timeout=12.0,
        )
        try:
            data = response.json()
        except ValueError:
            raise WatchVoiceError("watch_early_delta_invalid") from None
        if response.status_code == 409 and isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and error.get(".tag") == "reset":
                raise WatchVoiceError("watch_early_cursor_reset_required")
        if (
            response.status_code != 200
            or not isinstance(data, dict)
            or not isinstance(data.get("entries"), list)
            or not isinstance(data.get("cursor"), str)
            or not data["cursor"]
            or type(data.get("has_more")) is not bool
            or (data["has_more"] and data["cursor"] == page_cursor)
        ):
            raise WatchVoiceError("watch_early_delta_invalid")
        async with factory() as session:
            claimed = await session.execute(
                update(WatchEarlyCursor)
                .where(
                    WatchEarlyCursor.workspace_key == key["workspace_key"],
                    WatchEarlyCursor.account_id == key["account_id"],
                    WatchEarlyCursor.writer_mode == writer,
                    WatchEarlyCursor.writer_generation == generation,
                    WatchEarlyCursor.cursor == page_cursor,
                )
                .values(cursor=data["cursor"])
            )
            if not claimed.rowcount:  # type: ignore[attr-defined]
                return True
            for obj in data["entries"]:
                try:
                    item = metadata(obj)
                except WatchVoiceError as exc:
                    # Keep only validated identifiers and a digest, never source payload.
                    digest = hashlib.sha256(
                        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()
                    source_id = obj.get("id") if isinstance(obj, dict) else None
                    revision = obj.get("rev") if isinstance(obj, dict) else None
                    if not isinstance(source_id, str) or not re.fullmatch(
                        r"id:[A-Za-z0-9_-]{1,252}", source_id
                    ):
                        source_id = None
                    if not isinstance(revision, str) or not re.fullmatch(
                        r"[a-f0-9]{1,255}", revision
                    ):
                        revision = None
                    await session.execute(
                        insert(WatchEarlyRejection)
                        .values(
                            **key,
                            metadata_sha256=digest,
                            file_id=source_id,
                            revision=revision,
                            reason=exc.code,
                            detected_at=datetime.now(UTC),
                        )
                        .on_conflict_do_nothing()
                    )
                    continue
                if item is None or not after_cutover(
                    timestamp(item["server_modified"]), cutover_at
                ):
                    continue
                values = card_values(settings, item, datetime.now(UTC))
                if wake_received_at is not None and (
                    _utc(wake_received_at) >= values["source_modified_at"]
                ):
                    values["wake_received_at"] = wake_received_at
                await session.execute(
                    insert(WatchEarlyCard).values(**values).on_conflict_do_nothing()
                )
            await session.commit()
        page_cursor = data["cursor"]
        if not data["has_more"]:
            return False
    return True


async def drain_saved(
    factory: async_sessionmaker[AsyncSession],
    client: MarkInboxClient,
    key: dict[str, str],
    limit: int,
    *,
    writer: str = "legacy_actions",
) -> dict[str, Any]:
    """Publish at most the approved cap and return actual, never truncated counts."""
    from app.infrastructure.watch_voice_actions import result

    totals = {"considered": 0, "published_parts": 0, "delivered": 0, "reconciled": 0}
    last = "idle"
    for _ in range(limit):
        item = await publish_saved(factory, client, key, writer=writer)
        if item["result"] == "idle":
            break
        last = item["result"]
        for name in totals:
            totals[name] += item[name]
        # Unknown, rejected or contested work must not be selected repeatedly in the same run.
        if not item["published_parts"] and not item["reconciled"]:
            break
    return result(last, **totals)


async def publish_saved(
    factory: async_sessionmaker[AsyncSession],
    client: MarkInboxClient,
    key: dict[str, str],
    *,
    writer: str = "legacy_actions",
) -> dict[str, Any]:
    from app.infrastructure.watch_voice_actions import result

    async with factory() as session:
        cursor = await session.get(
            WatchEarlyCursor, (key["workspace_key"], key["account_id"]), with_for_update=True
        )
        if cursor is None or cursor.writer_mode != writer:
            return result("deferred")
        card = await session.scalar(
            select(WatchEarlyCard)
            .where(
                WatchEarlyCard.workspace_key == key["workspace_key"],
                WatchEarlyCard.account_id == key["account_id"],
                WatchEarlyCard.state.in_(("pending", "sending", "unknown")),
            )
            .order_by(
                case((WatchEarlyCard.state == "pending", 0), else_=1),
                WatchEarlyCard.detected_at,
                WatchEarlyCard.id,
            )
            .limit(1)
        )
        if card is None:
            return result("idle")
        was_pending = card.state == "pending"
        if was_pending:
            legacy = (
                await session.execute(
                    select(WatchVoiceNote.delivery_state, WatchVoiceNote.issue_url).where(
                        WatchVoiceNote.id == card.id,
                        WatchVoiceNote.workspace_key == key["workspace_key"],
                        WatchVoiceNote.account_id == key["account_id"],
                        WatchVoiceNote.file_id == card.file_id,
                        WatchVoiceNote.revision == card.revision,
                    )
                )
            ).one_or_none()
            if legacy is not None:
                match = re.fullmatch(
                    r"https://github\.com/markmalina/mark/issues/([1-9][0-9]{0,9})",
                    legacy.issue_url or "",
                )
                if legacy.delivery_state == "delivered" and match:
                    card.issue_number = int(match.group(1))
                    card.issue_url = legacy.issue_url
                    card.state = "saved"
                    await session.commit()
                    return result("processed", considered=1, reconciled=1)
                card.state = "blocked"
                card.last_error = "watch_early_legacy_receipt_required"
                await session.commit()
                return result("processed", considered=1)
            started_at = datetime.now(UTC)
            claimed = await session.execute(
                update(WatchEarlyCard)
                .where(WatchEarlyCard.id == card.id, WatchEarlyCard.state == "pending")
                .values(state="sending", attempt_started_at=started_at)
            )
            await session.commit()
            if not claimed.rowcount:  # type: ignore[attr-defined]
                return result("deferred")
        else:
            if card.state == "sending" and card.attempt_started_at is not None:
                if datetime.now(UTC) < _utc(card.attempt_started_at) + timedelta(seconds=180):
                    return result("deferred")
            # A stale attempt can only be reconciled by reading its receipt.
            await session.execute(
                update(WatchEarlyCard)
                .where(WatchEarlyCard.id == card.id, WatchEarlyCard.state == "sending")
                .values(state="unknown")
            )
            await session.commit()
        card_id, card_body, title = card.id, card.body, card.title
    try:
        async with asyncio.timeout(30 if was_pending else 20):
            receipt = (
                await client.post(part=0, issue_number=None, title=title, body=card_body)
                if was_pending
                else await client.reconcile(part=0, issue_number=None, body=card_body)
            )
    except TimeoutError:
        # A canceled HTTP request has an uncertain external outcome.
        async with factory() as session:
            await session.execute(
                update(WatchEarlyCard)
                .where(WatchEarlyCard.id == card_id, WatchEarlyCard.state == "sending")
                .values(state="unknown", last_error="watch_early_publish_timeout")
            )
            await session.commit()
        return result("processed", considered=1)
    except WatchVoiceError as exc:
        async with factory() as session:
            await session.execute(
                update(WatchEarlyCard)
                .where(
                    WatchEarlyCard.id == card_id, WatchEarlyCard.state.in_(("sending", "unknown"))
                )
                .values(
                    state="unknown" if exc.unknown or not was_pending else "blocked",
                    last_error=exc.code,
                )
            )
            await session.commit()
        return result("processed", considered=1)
    if receipt is None:
        return result("deferred", considered=1)
    async with factory() as session:
        await session.execute(
            update(WatchEarlyCard)
            .where(WatchEarlyCard.id == card_id, WatchEarlyCard.state.in_(("sending", "unknown")))
            .values(
                state="saved", issue_number=receipt.number, issue_url=receipt.url, last_error=None
            )
        )
        await session.commit()
    return result(
        "processed", considered=1, published_parts=int(was_pending), reconciled=int(not was_pending)
    )
