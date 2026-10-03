"""Synthetic formatting snapshots survive captions and durable sequence workers."""

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.application.reply_documents import (
    PreparedReplyDocument,
    PrepareReplyDocumentCommand,
    PrepareReplyDocumentService,
)
from app.application.reply_drafts import ReplyDraftError
from app.application.reply_sequences import (
    QueueApprovedReplySequenceCommand,
    QueueApprovedReplySequenceService,
    ReplySequenceError,
)
from app.application.telegram_formatting import (
    CAPTION_FORMAT_KEY,
    FORMAT_KEY,
    compile_text,
    stored_preview,
)
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_documents import SqlReplyDocumentWriter
from app.infrastructure.reply_sequences import SEQUENCE_CONTEXT_KEY, SqlReplySequenceWriter
from app.models.domain import ReplyDocument, ReplyDraft, SendAttempt
from app.worker.reply_sequences import (
    _discover_sequence_rows,
    _due_items,
    _sequence_row_from_scalars,
    run_reply_sequence_once,
)
from sqlalchemy import func, select

from test_reply_sends_postgres import (
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
    _sequence_command,
    _settings,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _CaptionWriter:
    command: PrepareReplyDocumentCommand | None = None

    async def save(
        self, command: PrepareReplyDocumentCommand, data: bytes
    ) -> PreparedReplyDocument:
        self.command = command
        return PreparedReplyDocument(
            draft_id=uuid4(),
            task_id=command.task_id,
            account_label="RU",
            recipient_chat_id="synthetic-recipient",
            filename=command.filename,
            media_type=command.media_type,
            size_bytes=len(data),
            sha256=command.expected_sha256,
            caption=command.caption,
            formatting=command.formatting,
        )


def _caption_command(caption: str, caption_format: str = "markdown") -> PrepareReplyDocumentCommand:
    data = b"%PDF-1.7\nsynthetic fixture"
    return PrepareReplyDocumentCommand(
        principal=Principal(
            subject="synthetic-owner",
            auth_method="test",
            scopes=frozenset({"gate:drafts:write"}),
            access_scope=AccessScope(
                workspace_id="synthetic-workspace",
                account_ids=frozenset({uuid4()}),
                project_ids=frozenset({uuid4()}),
            ),
        ),
        task_id=uuid4(),
        filename="synthetic.pdf",
        media_type="application/pdf",
        content_base64=base64.b64encode(data).decode("ascii"),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        caption=caption,
        caption_format=caption_format,
    )


@pytest.mark.anyio
async def test_caption_limit_applies_to_prepared_text_and_preserves_exact_snapshot() -> None:
    writer = _CaptionWriter()
    source = "**" + "я" * 1024 + "**"
    prepared = await PrepareReplyDocumentService(writer).execute(_caption_command(source))
    assert prepared.caption == "я" * 1024
    assert prepared.formatting is not None
    assert prepared.formatting.source_text == source
    assert prepared.formatting.entities == ({"type": "bold", "offset": 0, "length": 1024},)
    assert writer.command is not None and writer.command.formatting == prepared.formatting


@pytest.mark.anyio
@pytest.mark.parametrize("caption,caption_format", [("я" * 1025, "plain"), ("**x", "markdown")])
async def test_invalid_caption_never_reaches_staging(caption: str, caption_format: str) -> None:
    writer = _CaptionWriter()
    with pytest.raises(ReplyDraftError):
        await PrepareReplyDocumentService(writer).execute(_caption_command(caption, caption_format))
    assert writer.command is None


@pytest.mark.anyio
async def test_empty_plain_caption_is_an_explicit_snapshot() -> None:
    writer = _CaptionWriter()
    prepared = await PrepareReplyDocumentService(writer).execute(_caption_command("", "plain"))
    assert prepared.formatting is not None
    assert prepared.formatting.text == "" and prepared.formatting.entities == ()


@pytest.mark.anyio
async def test_document_caption_snapshot_persists_without_a_send() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        source = "💚 **Итог**\n[Ссылка](https://example.com/docs?a=1&b=2)"
        command = replace(
            _caption_command(source),
            principal=_save_draft_command(seed, "unused").principal,
            task_id=seed.task_id,
        )
        async with sessions() as session:
            prepared = await PrepareReplyDocumentService(SqlReplyDocumentWriter(session)).execute(
                command
            )
            await session.commit()
        async with sessions() as session:
            document = await session.scalar(
                select(ReplyDocument).where(ReplyDocument.reply_draft_id == prepared.draft_id)
            )
            draft = await session.get(ReplyDraft, prepared.draft_id)
            assert document is not None and draft is not None and prepared.formatting is not None
            assert document.caption == prepared.caption == "💚 Итог\nСсылка"
            snapshot = stored_preview(
                document.caption,
                draft.prompt_context,
                key=CAPTION_FORMAT_KEY,
                max_length=1024,
                allow_empty=True,
            )
            assert snapshot == prepared.formatting
            assert draft.prompt_context[CAPTION_FORMAT_KEY]["source_text"] == source
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("digests", [("0" * 64,), ("G" * 64, "0" * 64)])
async def test_sequence_digest_count_and_syntax_fail_before_writer(
    digests: tuple[str, ...],
) -> None:
    class _NoWriter:
        async def queue(self, command: QueueApprovedReplySequenceCommand) -> Any:
            raise AssertionError("Invalid approval reached writer")

    principal = replace(_caption_command("").principal, scopes=frozenset({"gate:messages:send"}))
    command = QueueApprovedReplySequenceCommand(
        principal=principal,
        draft_ids=(uuid4(), uuid4()),
        confirmed=True,
        expected_account="RU",
        expected_recipient_chat_id="synthetic-recipient",
        expected_payload_sha256s=digests,
    )
    with pytest.raises(ReplySequenceError) as error:
        await QueueApprovedReplySequenceService(_NoWriter()).execute(command)
    assert error.value.code == "invalid_confirmation"


def test_malformed_frozen_digest_does_not_drop_a_sequence_predecessor() -> None:
    now = datetime.now(UTC)
    first = _sequence_row_from_scalars(
        draft_id=uuid4(),
        task_id=uuid4(),
        conversation_id=uuid4(),
        account_id=uuid4(),
        project_id=uuid4(),
        attempt_status=None,
        attempt_finished_at=None,
        prompt_context={
            SEQUENCE_CONTEXT_KEY: {
                "id": "synthetic-sequence",
                "position": 0,
                "state": "scheduled",
                "scheduled_at": now.isoformat(),
                "interval_seconds": 60,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-recipient",
                "principal_subject": "synthetic-owner",
                "workspace_id": "synthetic-workspace",
                "payload_sha256": {"invalid": "type"},
            }
        },
    )
    assert first is not None
    assert _due_items((first,), now=now)[0].payload_sha256 == "invalid"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing", "reordered", "entities"])
async def test_sequence_rejects_unconfirmed_or_changed_formatting_atomically(failure: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        previews = (compile_text("**Первый**", "markdown"), compile_text("*Второй*", "markdown"))
        async with sessions() as session:
            for draft_id, preview in zip(seed.draft_ids, previews, strict=True):
                draft = await session.get(ReplyDraft, draft_id)
                assert draft is not None
                draft.draft_text = preview.text
                draft.prompt_context = {FORMAT_KEY: preview.to_dict()}
            await session.commit()
        digests = tuple(preview.payload_sha256 for preview in previews)
        if failure == "reordered":
            digests = tuple(reversed(digests))
        elif failure == "entities":
            async with sessions() as session:
                draft = await session.get(ReplyDraft, seed.draft_ids[1])
                assert draft is not None
                # A valid alternate formatting has identical text but another entity.
                draft.prompt_context = {
                    FORMAT_KEY: compile_text("**Второй**", "markdown").to_dict()
                }
                await session.commit()
        command = replace(
            _sequence_command(seed, seed.draft_ids),
            expected_payload_sha256s=None if failure == "missing" else digests,
        )
        async with sessions() as session:
            with pytest.raises(ReplySequenceError) as error:
                await SqlReplySequenceWriter(session).queue(command)
            assert error.value.code == (
                "formatting_confirmation_required"
                if failure == "missing"
                else "formatting_confirmation_mismatch"
            )
            await session.rollback()
        async with sessions() as session:
            for draft_id in seed.draft_ids:
                draft = await session.get(ReplyDraft, draft_id)
                assert draft is not None and SEQUENCE_CONTEXT_KEY not in draft.prompt_context
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
async def test_sequence_frozen_payload_survives_discovery_and_worker_replay() -> None:
    class _EntityTelegram(_LiteralRecordingTelegram):
        async def send_formatted_message(self, **kwargs: object) -> dict[str, object]:
            self.plain_calls.append(kwargs)
            return {"ok": True, "result": {"message_id": 800 + len(self.plain_calls)}}

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        previews = (compile_text("💚 **Первый**", "markdown"), compile_text("*Второй*", "markdown"))
        async with sessions() as session:
            for draft_id, preview in zip(seed.draft_ids, previews, strict=True):
                draft = await session.get(ReplyDraft, draft_id)
                assert draft is not None
                draft.draft_text = preview.text
                draft.prompt_context = {FORMAT_KEY: preview.to_dict()}
            await session.commit()
        now = datetime.now(UTC)
        digests = tuple(preview.payload_sha256 for preview in previews)
        async with sessions() as session:
            queued = await SqlReplySequenceWriter(session, now=now - timedelta(hours=1)).queue(
                replace(_sequence_command(seed, seed.draft_ids), expected_payload_sha256s=digests)
            )
            await session.commit()
        assert tuple(item.payload_sha256 for item in queued.items) == digests
        rows = await _discover_sequence_rows(sessions)
        assert _due_items(rows, now=now)[0].payload_sha256 == digests[0]
        telegram = _EntityTelegram()
        for minute in (0, 2):
            assert (
                await run_reply_sequence_once(
                    settings=_settings(),
                    session_factory=sessions,
                    telegram=telegram,
                    now=now + timedelta(minutes=minute),
                )
                == 1
            )
        assert (
            await run_reply_sequence_once(
                settings=_settings(),
                session_factory=sessions,
                telegram=telegram,
                now=now + timedelta(minutes=3),
            )
            == 0
        )
        assert [call["text"] for call in telegram.plain_calls] == [
            preview.text for preview in previews
        ]
        assert [call["entities"] for call in telegram.plain_calls] == [
            list(preview.entities) for preview in previews
        ]
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 2


@pytest.mark.anyio
async def test_entities_only_edit_after_queue_stops_unsent_tail_without_http() -> None:
    class _EntityTelegram(_LiteralRecordingTelegram):
        async def send_formatted_message(self, **kwargs: object) -> dict[str, object]:
            self.plain_calls.append(kwargs)
            return {"ok": True, "result": {"message_id": 800 + len(self.plain_calls)}}

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        previews = (compile_text("**Первый**", "markdown"), compile_text("*Второй*", "markdown"))
        async with sessions() as session:
            for draft_id, preview in zip(seed.draft_ids, previews, strict=True):
                draft = await session.get(ReplyDraft, draft_id)
                assert draft is not None
                draft.draft_text = preview.text
                draft.prompt_context = {FORMAT_KEY: preview.to_dict()}
            await session.commit()
        now = datetime.now(UTC)
        async with sessions() as session:
            await SqlReplySequenceWriter(session, now=now - timedelta(hours=1)).queue(
                replace(
                    _sequence_command(seed, seed.draft_ids),
                    expected_payload_sha256s=tuple(preview.payload_sha256 for preview in previews),
                )
            )
            await session.commit()
        async with sessions() as session:
            draft = await session.get(ReplyDraft, seed.draft_ids[1])
            assert draft is not None
            context = dict(draft.prompt_context)
            context[FORMAT_KEY] = compile_text("**Второй**", "markdown").to_dict()
            draft.prompt_context = context
            await session.commit()
        telegram = _EntityTelegram()
        assert (
            await run_reply_sequence_once(
                settings=_settings(), session_factory=sessions, telegram=telegram, now=now
            )
            == 1
        )
        assert (
            await run_reply_sequence_once(
                settings=_settings(),
                session_factory=sessions,
                telegram=telegram,
                now=now + timedelta(minutes=2),
            )
            == 0
        )
        assert len(telegram.plain_calls) == 1
        async with sessions() as session:
            draft = await session.get(ReplyDraft, seed.draft_ids[1])
            assert draft is not None
            assert draft.prompt_context[SEQUENCE_CONTEXT_KEY]["state"] == "stopped"
            assert (
                draft.prompt_context[SEQUENCE_CONTEXT_KEY]["error_code"]
                == "payload_confirmation_mismatch"
            )
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 1
