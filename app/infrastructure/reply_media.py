"""Transactional staging and scoped preview of immutable image packages."""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.reply_drafts import (
    ReplyDraftError,
    ReplyTaskNotFoundError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.reply_media import (
    MEDIA_CONVERTER_VERSION,
    MEDIA_PACKAGE_KEY,
    MEDIA_SCHEMA_VERSION,
    PreparedMediaItem,
    PreparedReplyMedia,
    PrepareReplyMediaCommand,
    ValidatedMediaItem,
    media_package_digest,
)
from app.application.reply_sends import ReplySendError
from app.core.mcp_security import Principal
from app.infrastructure.dialog_snapshots import require_dialog_snapshot
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.models.domain import Conversation, Message, ReplyDraft, ReplyTask, SendAttempt
from app.models.reply_media import ReplyMediaExecutionBudget, ReplyMediaItem, ReplyMediaPackage


class SqlReplyMediaWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        command: PrepareReplyMediaCommand,
        items: tuple[ValidatedMediaItem, ...],
        input_sha256: str,
    ) -> PreparedReplyMedia:
        scope = command.principal.access_scope
        conversation_id = await self._session.scalar(
            select(Conversation.id)
            .join(ReplyTask, ReplyTask.conversation_id == Conversation.id)
            .join(Message, ReplyTask.trigger_message_id == Message.id)
            .where(
                ReplyTask.id == command.task_id,
                ReplyTask.account_id.in_(scope.account_ids),
                ReplyTask.project_id.in_(scope.project_ids),
                Conversation.account_id.in_(scope.account_ids),
                Conversation.project_id.in_(scope.project_ids),
                Message.account_id.in_(scope.account_ids),
                Message.project_id.in_(scope.project_ids),
            )
            .with_for_update(of=Conversation)
        )
        if conversation_id is None:
            raise ReplyTaskNotFoundError()
        existing = await self._session.scalar(
            select(ReplyMediaPackage).where(
                ReplyMediaPackage.workspace_id == scope.workspace_id,
                ReplyMediaPackage.principal_subject == command.principal.subject,
                ReplyMediaPackage.staging_key == command.staging_key,
            )
        )
        if existing is not None:
            if existing.input_sha256 != input_sha256:
                raise ReplyDraftError(
                    code="media_staging_conflict",
                    message="Staging key belongs to another immutable package",
                )
            draft = await self._session.scalar(
                select(ReplyDraft).where(
                    ReplyDraft.id == existing.reply_draft_id,
                    ReplyDraft.reply_task_id == command.task_id,
                    ReplyDraft.account_id.in_(scope.account_ids),
                    ReplyDraft.project_id.in_(scope.project_ids),
                )
            )
            if draft is None:
                raise ReplyTaskNotFoundError()
            attempted = await self._session.scalar(
                select(SendAttempt.id).where(
                    SendAttempt.reply_draft_id == draft.id,
                )
            )
            if attempted is None:
                conversation = await self._session.get(Conversation, conversation_id)
                assert conversation is not None
                try:
                    await require_dialog_snapshot(
                        self._session,
                        scope=scope,
                        conversation=conversation,
                        expected=command.expected_context_snapshot,
                    )
                except ReplySendError as exc:
                    raise ReplyDraftError(code=exc.code, message=exc.message) from exc
            models = await _items(self._session, existing.id)
            _assert_integrity(existing, draft, models)
            # No expiry refresh and no reload of bytes on an exact staging replay.
            return _prepared(existing, draft, models, replay=True)
        budget = await _ensure_budget(
            self._session, command.principal, command.execution_id, command.execution_expires_at
        )
        package_id = uuid4()
        draft_text = f"Media package {package_id}"
        # Nested transaction keeps a race on the unique staging key from leaving
        # an orphan draft or a partially staged package in the caller transaction.
        try:
            async with self._session.begin_nested():
                saved = await SaveReplyDraftService(SqlReplyDraftWriter(self._session)).execute(
                    SaveReplyDraftCommand(
                        principal=command.principal,
                        task_id=command.task_id,
                        text=draft_text,
                        follow_up=command.follow_up,
                        expected_context_snapshot=command.expected_context_snapshot,
                    )
                )
                draft = await self._session.scalar(
                    select(ReplyDraft).where(ReplyDraft.id == saved.draft_id).with_for_update()
                )
                if draft is None:
                    raise RuntimeError("Media draft disappeared")
                connection_id, recipient_label = (
                    await self._session.execute(
                        select(Conversation.business_connection_id, Conversation.title).where(
                            Conversation.id == conversation_id
                        )
                    )
                ).one()
                if not connection_id:
                    connection_id = await self._session.scalar(
                        select(Message.business_connection_id).where(
                            Message.id == draft.trigger_message_id
                        )
                    )
                if not connection_id:
                    raise ReplyDraftError(
                        code="origin_connection_missing",
                        message="Media requires an originating Business connection",
                    )
                models = [
                    _make_item(item, package_id, ordinal)
                    for ordinal, item in enumerate(items, start=1)
                ]
                metadata = [_metadata(model).to_dict() for model in models]
                rich: dict[str, Any] | None = None
                if command.rich_content is not None:
                    from app.application.telegram_formatting import FormattingError
                    from app.application.telegram_rich_media import compile_rich_media

                    normalized = _normalize_rich(command.rich_content, models, command.mode)
                    try:
                        rich = compile_rich_media(normalized, metadata)
                    except FormattingError as exc:
                        raise ReplyDraftError(code=exc.code, message=exc.message) from exc
                expires_at = min(datetime.now(UTC) + timedelta(hours=24), budget.expires_at)
                expected_count = len(items) if command.mode == "album" else 1
                snapshot: dict[str, Any] = {
                    "schema_version": MEDIA_SCHEMA_VERSION,
                    "context_snapshot": command.expected_context_snapshot,
                    "converter_version": MEDIA_CONVERTER_VERSION,
                    "package_id": str(package_id),
                    "mode": command.mode,
                    "account_label": saved.account_label,
                    "recipient_label": recipient_label,
                    "execution_expires_at": _utc(budget.expires_at).isoformat(),
                    "draft_text": draft_text,
                    "expected_message_count": expected_count,
                    "scope": {
                        "workspace_id": scope.workspace_id,
                        "principal_subject": command.principal.subject,
                        "account_id": str(saved.account_id),
                        "project_id": str(saved.project_id),
                        "recipient_chat_id": saved.recipient_chat_id,
                        "connection_id": connection_id,
                        "draft_id": str(saved.draft_id),
                        "draft_revision": 1,
                        "execution_id": command.execution_id,
                    },
                    "items": metadata,
                    "rich": rich,
                }
                digest = media_package_digest(snapshot)
                snapshot["package_sha256"] = digest
                package = ReplyMediaPackage(
                    id=package_id,
                    reply_draft_id=draft.id,
                    workspace_id=scope.workspace_id,
                    principal_subject=command.principal.subject,
                    staging_key=command.staging_key,
                    execution_id=command.execution_id,
                    input_sha256=input_sha256,
                    mode=command.mode,
                    package_sha256=digest,
                    snapshot_json=snapshot,
                    expected_message_count=expected_count,
                    expires_at=expires_at,
                    receipt_json={},
                )
                draft.source = "chatgpt-mcp-media"
                draft.prompt_context = {
                    **draft.prompt_context,
                    "kind": "media",
                    MEDIA_PACKAGE_KEY: {
                        "package_id": str(package_id),
                        "package_sha256": digest,
                        "mode": command.mode,
                    },
                }
                self._session.add(package)
                await self._session.flush()
                self._session.add_all(models)
                await self._session.flush()
        except IntegrityError as exc:
            raise ReplyDraftError(
                code="media_staging_conflict",
                message="Concurrent staging key conflict; reread the package",
            ) from exc
        return _prepared(
            package, draft, models, replay=False, account_label_value=saved.account_label
        )


async def _ensure_budget(
    session: AsyncSession,
    principal: Principal,
    execution_id: str,
    execution_expires_at: datetime,
) -> ReplyMediaExecutionBudget:
    where = (
        ReplyMediaExecutionBudget.workspace_id == principal.access_scope.workspace_id,
        ReplyMediaExecutionBudget.principal_subject == principal.subject,
        ReplyMediaExecutionBudget.execution_id == execution_id,
    )
    budget = await session.scalar(select(ReplyMediaExecutionBudget).where(*where))
    if budget is None:
        try:
            async with session.begin_nested():
                budget = ReplyMediaExecutionBudget(
                    id=uuid4(),
                    workspace_id=principal.access_scope.workspace_id,
                    principal_subject=principal.subject,
                    execution_id=execution_id,
                    expires_at=execution_expires_at.astimezone(UTC),
                    write_cap=12,
                    message_cap=20,
                    writes_reserved=0,
                    messages_reserved=0,
                )
                session.add(budget)
                await session.flush()
        except IntegrityError:
            budget = await session.scalar(select(ReplyMediaExecutionBudget).where(*where))
    if budget is None:
        raise RuntimeError("Media execution budget disappeared")
    if _utc(budget.expires_at) <= datetime.now(UTC):
        raise ReplyDraftError(code="media_execution_expired", message="Media execution has expired")
    if _utc(budget.expires_at) != execution_expires_at.astimezone(UTC):
        raise ReplyDraftError(
            code="media_execution_conflict", message="Execution deadline is immutable"
        )
    return budget


def _normalize_rich(
    source: dict[str, Any],
    models: list[ReplyMediaItem],
    mode: str,
) -> dict[str, Any]:
    result = copy.deepcopy(source)
    seen: list[int] = []
    media_types: list[str] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 8:
            raise ReplyDraftError(code="invalid_media", message="Rich media exceeds nesting limit")
        if isinstance(value, dict):
            kind = value.get("type")
            if kind in {"photo", "slideshow", "collage"}:
                media_types.append(kind)
            if kind == "photo":
                if "version" in value and (
                    type(value["version"]) is not int or value["version"] != 1
                ):
                    raise ReplyDraftError(
                        code="invalid_media_ref", message="Unsupported prepared media version"
                    )
                reference = value.get("media_ref")
                matches = [
                    index for index in range(1, len(models) + 1) if reference == f"item:{index}"
                ]
                if len(matches) != 1:
                    raise ReplyDraftError(
                        code="invalid_media_ref",
                        message="Prepare media refs must be local item:N aliases",
                    )
                index = matches[0]
                model = models[index - 1]
                if value.get("sha256", model.source_sha256) != model.source_sha256:
                    raise ReplyDraftError(
                        code="media_changed", message="Rich source image hash differs"
                    )
                seen.append(index)
                value["media_ref"] = str(model.id)
                value["sha256"] = model.output_sha256
                value["version"] = 1
            for child in value.values():
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(result)
    expected_types = (
        ["photo"]
        if mode == "rich_photo"
        else [mode.removeprefix("rich_"), *(["photo"] * len(models))]
    )
    if seen != list(range(1, len(models) + 1)) or media_types != expected_types:
        raise ReplyDraftError(
            code="invalid_media_ref",
            message="Rich mode must use all package images exactly once in order",
        )
    return result


def _make_item(item: ValidatedMediaItem, package_id: UUID, ordinal: int) -> ReplyMediaItem:
    return ReplyMediaItem(
        id=uuid4(),
        package_id=package_id,
        ordinal=ordinal,
        filename=item.filename,
        media_type=item.media_type,
        source_size_bytes=item.source_size_bytes,
        size_bytes=item.size_bytes,
        source_sha256=item.source_sha256,
        output_sha256=item.output_sha256,
        width=item.width,
        height=item.height,
        transform_version=item.transform_version,
        transform=item.transform,
        caption=item.caption,
        caption_format=item.caption_format,
        caption_formatting_json=item.caption_formatting_json,
        data=item.data,
        receipt_json={},
    )


def _metadata(model: ReplyMediaItem) -> PreparedMediaItem:
    return PreparedMediaItem(
        media_ref=str(model.id),
        ordinal=model.ordinal,
        filename=model.filename,
        media_type=model.media_type,
        source_size_bytes=model.source_size_bytes,
        size_bytes=model.size_bytes,
        source_sha256=model.source_sha256,
        output_sha256=model.output_sha256,
        width=model.width,
        height=model.height,
        transform_version=model.transform_version,
        transform=model.transform,
        caption=model.caption,
        caption_format=model.caption_format,
        caption_formatting_json=model.caption_formatting_json,
    )


async def _items(session: AsyncSession, package_id: UUID) -> list[ReplyMediaItem]:
    result = await session.scalars(
        select(ReplyMediaItem)
        .where(ReplyMediaItem.package_id == package_id)
        .order_by(ReplyMediaItem.ordinal)
    )
    return list(result.all())


def _assert_integrity(
    package: ReplyMediaPackage,
    draft: ReplyDraft,
    items: list[ReplyMediaItem],
) -> None:
    snapshot = package.snapshot_json
    sentinel = {
        "package_id": str(package.id),
        "package_sha256": package.package_sha256,
        "mode": package.mode,
    }
    scope = snapshot.get("scope", {})
    if (
        media_package_digest(snapshot) != package.package_sha256
        or snapshot.get("package_sha256") != package.package_sha256
        or snapshot.get("items") != [_metadata(item).to_dict() for item in items]
        or draft.prompt_context.get(MEDIA_PACKAGE_KEY) != sentinel
        or snapshot.get("draft_text") != draft.draft_text
        or scope.get("account_id") != str(draft.account_id)
        or scope.get("project_id") != str(draft.project_id)
        or scope.get("draft_id") != str(draft.id)
        or scope.get("workspace_id") != package.workspace_id
        or scope.get("principal_subject") != package.principal_subject
        or scope.get("execution_id") != package.execution_id
    ):
        raise ReplyDraftError(code="media_changed", message="Saved media package integrity differs")


def _prepared(
    package: ReplyMediaPackage,
    draft: ReplyDraft,
    items: list[ReplyMediaItem],
    *,
    replay: bool,
    account_label_value: str | None = None,
) -> PreparedReplyMedia:
    scope = package.snapshot_json["scope"]
    # Label is saved in the immutable snapshot, never guessed from an opaque UUID.
    label = account_label_value or package.snapshot_json.get("account_label", "")
    return PreparedReplyMedia(
        draft_id=draft.id,
        package_id=package.id,
        task_id=draft.reply_task_id,
        account_label=label,
        recipient_chat_id=scope["recipient_chat_id"],
        mode=package.mode,
        package_sha256=package.package_sha256,
        expected_message_count=package.expected_message_count,
        expires_at=package.expires_at,
        items=tuple(_metadata(item) for item in items),
        rich_content=package.snapshot_json["rich"]["source_content"]
        if package.snapshot_json.get("rich")
        else None,
        execution_id=package.execution_id,
        execution_expires_at=datetime.fromisoformat(package.snapshot_json["execution_expires_at"]),
        idempotent_replay=replay,
    )


async def read_reply_media_preview(
    session: AsyncSession,
    principal: Principal,
    package_id: UUID,
) -> tuple[tuple[PreparedMediaItem, bytes], ...]:
    if not principal.access_scope.is_explicit or not (
        {"gate:drafts:write", "gate:inbox:read"} & principal.scopes
    ):
        raise ReplyDraftError(
            code="insufficient_scope", message="Media preview requires scoped access"
        )
    scope = principal.access_scope
    row = (
        await session.execute(
            select(ReplyMediaPackage, ReplyDraft)
            .join(ReplyDraft, ReplyMediaPackage.reply_draft_id == ReplyDraft.id)
            .where(
                ReplyMediaPackage.id == package_id,
                ReplyMediaPackage.workspace_id == scope.workspace_id,
                ReplyMediaPackage.principal_subject == principal.subject,
                ReplyDraft.account_id.in_(scope.account_ids),
                ReplyDraft.project_id.in_(scope.project_ids),
            )
        )
    ).one_or_none()
    if row is None:
        raise ReplyDraftError(code="media_not_found", message="Media package not found in scope")
    package, draft = row
    if _utc(package.expires_at) <= datetime.now(UTC):
        raise ReplyDraftError(code="media_unavailable", message="Media preview expired")
    items = await _items(session, package.id)
    _assert_integrity(package, draft, items)
    result: list[tuple[PreparedMediaItem, bytes]] = []
    for item in items:
        if (
            item.data is None
            or len(item.data) != item.size_bytes
            or hashlib.sha256(item.data).hexdigest() != item.output_sha256
        ):
            raise ReplyDraftError(
                code="media_unavailable", message="Prepared media bytes unavailable"
            )
        result.append((_metadata(item), item.data))
    return tuple(result)


async def prune_expired_reply_media(session: AsyncSession) -> int:
    """Clear bytes only after expiry and after every unresolved send is terminal.

    Lock the same draft row as send reservation before inspecting the attempt.
    Thus cleanup cannot erase bytes between send validation and reservation.
    Unknown/in-flight operations keep bytes until explicit reconciliation.
    """
    candidates = await session.scalars(
        select(ReplyDraft.id)
        .join(ReplyMediaPackage, ReplyMediaPackage.reply_draft_id == ReplyDraft.id)
        .where(ReplyMediaPackage.expires_at <= datetime.now(UTC))
        .order_by(ReplyDraft.id)
        .with_for_update(of=ReplyDraft, skip_locked=True)
    )
    cleared = 0
    for draft_id in candidates.all():
        status = await session.scalar(
            select(SendAttempt.status).where(SendAttempt.reply_draft_id == draft_id)
        )
        if status in {"reserved", "sending", "unknown"}:
            continue
        package = await session.scalar(
            select(ReplyMediaPackage)
            .where(ReplyMediaPackage.reply_draft_id == draft_id)
            .with_for_update()
        )
        if package is None:
            continue
        for item in await _items(session, package.id):
            if item.data is not None:
                item.data = None
                cleared += 1
    await session.flush()
    return cleared


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
