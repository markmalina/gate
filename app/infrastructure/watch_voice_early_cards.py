"""Default-off metadata-only Watch inbox. Never downloads or transcribes audio."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.watch_voice_github import MarkInboxClient, WatchVoiceError, marker
from app.infrastructure.watch_voice_recovery import _utc
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor, WatchEarlyRejection
from app.worker.dropbox_calls import _refresh_access_token, _verify_account_id
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
    # Imported lazily to keep the actions module as the existing response contract.
    from app.infrastructure.watch_voice_actions import result

    key = {
        "workspace_key": settings.gate_workspace_id,
        "account_id": settings.dropbox_call_account_id,
    }
    async with factory() as session:
        await session.execute(
            insert(WatchEarlyCursor)
            .values(**key, cutover_at=datetime.now(UTC))
            .on_conflict_do_nothing()
        )
        await session.commit()
    async with factory() as session:
        cursor = await session.scalar(
            select(WatchEarlyCursor)
            .where(
                WatchEarlyCursor.workspace_key == key["workspace_key"],
                WatchEarlyCursor.account_id == key["account_id"],
            )
            .with_for_update(skip_locked=True)
        )
        if cursor is None:
            return result("deferred")
        token = await _refresh_access_token(settings, http)
        await _verify_account_id(settings, http, token)
        page_cursor = cursor.cursor
        for _ in range(100):
            endpoint = "list_folder/continue" if page_cursor else "list_folder"
            body = (
                {"cursor": page_cursor}
                if page_cursor
                else {"path": PATH, "recursive": False, "include_deleted": False}
            )
            try:
                response = await http.post(
                    "https://api.dropboxapi.com/2/files/" + endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                    follow_redirects=False,
                    timeout=30.0,
                )
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise WatchVoiceError("watch_early_delta_unavailable") from None
            if (
                response.status_code != 200
                or not isinstance(data, dict)
                or not isinstance(data.get("entries"), list)
                or not isinstance(data.get("cursor"), str)
                or not data["cursor"]
                or type(data.get("has_more")) is not bool
            ):
                raise WatchVoiceError("watch_early_delta_invalid")
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
                    timestamp(item["server_modified"]), cursor.cutover_at
                ):
                    continue
                values = card_values(settings, item, datetime.now(UTC))
                await session.execute(
                    insert(WatchEarlyCard).values(**values).on_conflict_do_nothing()
                )
            page_cursor = data["cursor"]
            if not data["has_more"]:
                break
        else:
            raise WatchVoiceError("watch_early_page_limit")
        # Metadata and cursor advance commit atomically; no audio bytes are fetched.
        cursor.cursor = page_cursor
        await session.commit()
    async with factory() as session:
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
            claimed = await session.execute(
                update(WatchEarlyCard)
                .where(WatchEarlyCard.id == card.id, WatchEarlyCard.state == "pending")
                .values(state="sending")
            )
            await session.commit()
            if not claimed.rowcount:  # type: ignore[attr-defined]
                return result("deferred")
        else:
            # A lost or in-flight POST is never repeated: only exact-body reconciliation.
            await session.execute(
                update(WatchEarlyCard)
                .where(WatchEarlyCard.id == card.id, WatchEarlyCard.state == "sending")
                .values(state="unknown")
            )
            await session.commit()
        card_id, card_body, title = card.id, card.body, card.title
    try:
        receipt = (
            await client.post(part=0, issue_number=None, title=title, body=card_body)
            if was_pending
            else await client.reconcile(part=0, issue_number=None, body=card_body)
        )
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
