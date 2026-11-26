"""Saved formatting consent and exact once-only provider payload, with isolated PostgreSQL."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

import httpx
import pytest
from app.application.reply_drafts import SaveReplyDraftService
from app.application.reply_sends import ReplySendError, SendApprovedReplyService
from app.application.telegram_formatting import (
    CAPTION_FORMAT_KEY,
    FORMAT_KEY,
    compile_text,
)
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.models.domain import ReplyDocument, ReplyDraft, SendAttempt
from app.services.telegram import TelegramApiError
from sqlalchemy import func, select, update

from test_reply_sends_postgres import (
    _attach_document,
    _command,
    _document_command,
    _DocumentTelegram,
    _live_connection,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
    _settings,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RecordingTelegram:
    def __init__(self, outcome: str = "sent") -> None:
        self.outcome = outcome
        self.probes: list[str] = []
        self.calls: list[dict[str, Any]] = []

    async def get_business_connection(self, *, business_connection_id: str) -> dict[str, Any]:
        self.probes.append(business_connection_id)
        return _live_connection(business_connection_id)

    async def send_formatted_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.outcome == "unknown":
            raise httpx.ReadTimeout("ambiguous delivery")
        if self.outcome == "failed":
            raise TelegramApiError(error_code=400, description="Invalid entity", http_status=400)
        return {"ok": True, "result": {"message_id": 444}}

    async def send_plain_message(self, **kwargs: Any) -> dict[str, Any]:
        return await self.send_formatted_message(**kwargs)


@pytest.mark.anyio
@pytest.mark.parametrize("text_format", ["plain", "markdown"])
@pytest.mark.parametrize("outcome", ["sent", "failed", "unknown"])
async def test_preview_equals_reserved_payload_and_replay_never_resends(
    text_format: str,
    outcome: str,
) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with sessions() as session:
            saved = await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
                replace(
                    _save_draft_command(seed, "💚 **Привет**\nПроверка"), text_format=text_format
                )
            )
            await session.commit()
        assert saved.formatting is not None
        assert saved.formatting.source_text == "💚 **Привет**\nПроверка"
        command = replace(
            _command(seed, saved.draft_id),
            expected_payload_sha256=saved.formatting.payload_sha256,
        )
        telegram = RecordingTelegram(outcome)
        results = []
        for _ in range(2):
            async with sessions() as session:
                results.append(
                    await SqlTelegramReplySender(
                        session=session,
                        telegram=telegram,
                        settings=_settings(),
                    ).send(command)
                )
        assert results[0].status == outcome
        assert results[1].idempotent_replay is True
        assert results[0].attempt_id == results[1].attempt_id
        assert results[0].payload_sha256 == saved.formatting.payload_sha256
        assert len(telegram.calls) == 1
        assert len(telegram.probes) == 1
        assert telegram.calls[0]["text"] == saved.text
        if text_format == "markdown":
            assert telegram.calls[0]["entities"] == list(saved.formatting.entities)
            assert telegram.calls[0]["entities"][0]["offset"] == 3  # emoji UTF-16 units
        else:
            assert "entities" not in telegram.calls[0]
            assert "**Привет**" in telegram.calls[0]["text"]
        assert "parse_mode" not in telegram.calls[0]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case", ["missing_consent", "wrong_consent", "text", "entities", "version"]
)
async def test_invalid_or_changed_snapshot_fails_before_any_provider_io(case: str) -> None:
    async with _postgres_sessions() as sessions:
        preview = compile_text("**Привет**", "markdown")
        seed = await _seed_reply(sessions, draft_text=preview.text)
        snapshot = preview.to_dict()
        if case == "entities":
            snapshot["entities"][0]["type"] = "italic"
        elif case == "version":
            snapshot["converter_version"] = "unreviewed-version"
        async with sessions() as session:
            await session.execute(
                update(ReplyDraft)
                .where(ReplyDraft.id == seed.draft_ids[0])
                .values(
                    prompt_context={"dialog_snapshot": seed.context_snapshot, FORMAT_KEY: snapshot},
                    draft_text="Подменённый текст" if case == "text" else preview.text,
                )
            )
            await session.commit()
        digest = (
            None
            if case == "missing_consent"
            else ("0" * 64 if case == "wrong_consent" else preview.payload_sha256)
        )
        telegram = RecordingTelegram()
        async with sessions() as session:
            with pytest.raises(ReplySendError):
                await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=_settings(),
                ).send(replace(_command(seed, seed.draft_ids[0]), expected_payload_sha256=digest))
        assert telegram.probes == [] and telegram.calls == []
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
async def test_new_valid_snapshot_cannot_relabel_already_sent_attempt() -> None:
    async with _postgres_sessions() as sessions:
        original = compile_text("**Original**", "markdown")
        replacement = compile_text("*Original*", "markdown")
        seed = await _seed_reply(sessions, draft_text=original.text)
        async with sessions() as session:
            await session.execute(
                update(ReplyDraft)
                .where(ReplyDraft.id == seed.draft_ids[0])
                .values(
                    prompt_context={
                        "dialog_snapshot": seed.context_snapshot, FORMAT_KEY: original.to_dict(),
                    },
                )
            )
            await session.commit()
        telegram = RecordingTelegram()
        async with sessions() as session:
            await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings()
            ).send(
                replace(
                    _command(seed, seed.draft_ids[0]),
                    expected_payload_sha256=original.payload_sha256,
                )
            )
        async with sessions() as session:
            await session.execute(
                update(ReplyDraft)
                .where(ReplyDraft.id == seed.draft_ids[0])
                .values(
                    prompt_context={
                        "dialog_snapshot": seed.context_snapshot, FORMAT_KEY: replacement.to_dict(),
                    },
                )
            )
            await session.commit()
        async with sessions() as session:
            with pytest.raises(ReplySendError, match="another saved formatting payload"):
                await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=_settings(),
                ).send(
                    replace(
                        _command(seed, seed.draft_ids[0]),
                        expected_payload_sha256=replacement.payload_sha256,
                    )
                )
        assert len(telegram.calls) == 1
        assert len(telegram.probes) == 1


@pytest.mark.anyio
async def test_document_caption_uses_confirmed_saved_entities_and_exact_replay() -> None:
    from app.core.config import Settings

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        data = await _attach_document(sessions, seed)
        preview = compile_text("💚 **Ready**", "markdown", max_length=1024, allow_empty=True)
        async with sessions() as session:
            draft = await session.get(ReplyDraft, seed.draft_ids[0])
            assert draft is not None
            metadata = deepcopy(draft.prompt_context)
            metadata[CAPTION_FORMAT_KEY] = preview.to_dict()
            draft.prompt_context = metadata
            await session.execute(
                update(ReplyDocument)
                .where(
                    ReplyDocument.reply_draft_id == draft.id,
                )
                .values(caption=preview.text)
            )
            await session.commit()
        command = replace(
            _document_command(seed, data),
            expected_document_caption=preview.text,
            expected_payload_sha256=preview.payload_sha256,
        )
        telegram = _DocumentTelegram()
        for _ in range(2):
            async with sessions() as session:
                result = await SqlTelegramReplySender(
                    session=session,
                    telegram=telegram,
                    settings=Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True),
                ).send(command)
            assert result.sent
        assert result.idempotent_replay
        assert len(telegram.calls) == 1
        assert telegram.calls[0]["caption"] == preview.text
        assert telegram.calls[0]["caption_entities"] == list(preview.entities)
        assert "parse_mode" not in telegram.calls[0]


@pytest.mark.anyio
async def test_service_rejects_invalid_digest_before_executor() -> None:
    from test_reply_send_application import CapturingExecutor
    from test_reply_send_application import _command as application_command

    executor = CapturingExecutor()
    with pytest.raises(ReplySendError) as exc:
        await SendApprovedReplyService(executor).execute(
            replace(application_command(), expected_payload_sha256="not-a-digest")
        )
    assert exc.value.code == "invalid_confirmation"
    assert executor.commands == []


@pytest.mark.anyio
async def test_application_compiles_once_and_never_trusts_caller_supplied_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.application.reply_drafts as application

    from test_reply_draft_application import (
        TASK_ID,
        CapturingWriter,
        SaveReplyDraftCommand,
        _principal,
    )

    calls: list[tuple[str, str]] = []

    def counted_compile(source_text: str, text_format: str = "plain") -> Any:
        calls.append((source_text, text_format))
        return compile_text(source_text, text_format)

    monkeypatch.setattr(application, "compile_text", counted_compile)
    writer = CapturingWriter()
    supplied = compile_text("**Unauthorized**", "markdown")
    await SaveReplyDraftService(writer).execute(
        SaveReplyDraftCommand(
            principal=_principal(scopes=frozenset({"gate:drafts:write"})),
            task_id=TASK_ID,
            text="💚 **Approved**",
            text_format="markdown",
            formatting=supplied,
        )
    )
    assert calls == [("💚 **Approved**", "markdown")]
    command = writer.commands[0]
    assert command.text == "💚 Approved"
    assert command.formatting is not None
    assert command.formatting.source_text == "💚 **Approved**"
    assert command.formatting.payload_sha256 != supplied.payload_sha256


def _context(preview: Any = None) -> Any:
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.infrastructure.reply_sends import _SendContextSnapshot

    return _SendContextSnapshot(
        draft_id=uuid4(),
        draft_text=preview.text if preview else "legacy",
        draft_status="generated",
        draft_updated_at=datetime.now(UTC),
        task_id=uuid4(),
        task_status="needs_reply",
        conversation_id=uuid4(),
        conversation_business_connection_id="test-connection",
        conversation_last_inbound_at=datetime.now(UTC),
        trigger_message_id=uuid4(),
        trigger_business_connection_id="test-connection",
        target_chat_id="12345",
        account_id=uuid4(),
        project_id=uuid4(),
        account_alias="telegram_mark_ru",
        account_label="RU",
        account_is_active=True,
        account_send_enabled=True,
        bot_id=uuid4(),
        person_id=None,
        account_telegram_user_id=1,
        counterparty_display_name=None,
        formatting=preview,
    )


@pytest.mark.parametrize("text_format", ["plain", "markdown"])
def test_snapshot_confirmation_is_exact_for_all_new_formats(text_format: str) -> None:
    from test_reply_send_application import _command as application_command

    preview = compile_text("💚 **Approved**", text_format)
    context = _context(preview)
    sender = SqlTelegramReplySender(session=None, telegram=None, settings=_settings())
    for digest, error in (
        (None, "payload_confirmation_required"),
        ("0" * 64, "payload_confirmation_mismatch"),
    ):
        with pytest.raises(ReplySendError) as exc:
            sender._validate_confirmation(
                command=replace(application_command(), expected_payload_sha256=digest),
                account_label_value="RU",
                target_chat_id="12345",
                context=context,
            )
        assert exc.value.code == error
    sender._validate_confirmation(
        command=replace(application_command(), expected_payload_sha256=preview.payload_sha256),
        account_label_value="RU",
        target_chat_id="12345",
        context=context,
    )


def test_reserved_preview_revalidates_entities_without_rerendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.application.telegram_formatting as formatter

    preview = compile_text("**Approved**", "markdown")
    context = _context(preview)

    def forbid_compile(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Sending must never rerender Markdown")

    monkeypatch.setattr(formatter, "compile_text", forbid_compile)
    SqlTelegramReplySender._validate_reserved_formatting(context)
    preview.entities[0]["type"] = "italic"
    with pytest.raises(ReplySendError):
        SqlTelegramReplySender._validate_reserved_formatting(context)
