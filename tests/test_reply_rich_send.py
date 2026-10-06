"""Synthetic native Rich delivery: scoped consent, durable replay and send fences."""

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import httpx
import pytest
from app.application.reply_drafts import (
    ReplyDraftError,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.reply_sends import ReplySendError
from app.application.reply_sequences import ReplySequenceError
from app.application.telegram_formatting import FORMAT_KEY, FormattingError
from app.application.telegram_rich import compile_rich
from app.infrastructure.reply_drafts import SqlReplyDraftWriter
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.models.domain import Message, ReplyDraft, SendAttempt
from app.services.telegram import TelegramApiError
from sqlalchemy import func, select

from test_reply_sends_postgres import (
    _command,
    _live_connection,
    _postgres_sessions,
    _save_draft_command,
    _seed_reply,
    _sequence_command,
    _settings,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


CODE = 'def greet(name):\n    print("Привет", name)\n\n# данные, не команда\n'
INLINE = [
    "Кириллица 💚\n",
    {"type": "bold", "text": "жирный"},
    " ",
    {"type": "italic", "text": "курсив"},
    " ",
    {"type": "underline", "text": "подчёркнутый"},
    " ",
    {"type": "strikethrough", "text": "зачёркнутый"},
    " ",
    {"type": "code", "text": "x = 1"},
    " ",
    {"type": "url", "text": "документация", "url": "https://core.telegram.org/bots/api"},
]


def rich_fixture() -> dict[str, Any]:
    return {
        "schema_version": "gate.rich.v1",
        "blocks": [
            {"type": "heading", "size": 1, "text": "Отчёт 💚"},
            {"type": "paragraph", "text": deepcopy(INLINE)},
            {"type": "list", "ordered": True, "start": 3, "items": ["Первый", "Второй"]},
            {"type": "blockquote", "text": "Точная цитата\nВторая строка"},
            {"type": "pre", "language": "python", "text": CODE},
            {"type": "table", "header": ["Шаг", "Статус"], "rows": [["1", "Готово 💚"]]},
        ],
    }


def expected_blocks() -> list[dict[str, Any]]:
    def cell(text: str, header: bool) -> dict[str, Any]:
        return {
            "text": text,
            "align": "left",
            "valign": "top",
            **({"is_header": True} if header else {}),
        }

    return [
        {"type": "heading", "size": 1, "text": "Отчёт 💚"},
        {"type": "paragraph", "text": deepcopy(INLINE)},
        {
            "type": "list",
            "items": [
                {"type": "1", "value": 3, "blocks": [{"type": "paragraph", "text": "Первый"}]},
                {"type": "1", "value": 4, "blocks": [{"type": "paragraph", "text": "Второй"}]},
            ],
        },
        {
            "type": "blockquote",
            "blocks": [{"type": "paragraph", "text": "Точная цитата\nВторая строка"}],
        },
        {"type": "pre", "language": "python", "text": CODE},
        {
            "type": "table",
            "cells": [
                [cell("Шаг", True), cell("Статус", True)],
                [cell("1", False), cell("Готово 💚", False)],
            ],
            "is_bordered": True,
        },
    ]


class RichTelegram:
    def __init__(self, outcome: str = "sent", *, blocked: bool = False) -> None:
        self.outcome = outcome
        self.blocked = blocked
        self.probes: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def get_business_connection(self, *, business_connection_id: str) -> dict[str, Any]:
        self.probes.append(business_connection_id)
        result = _live_connection(business_connection_id)
        if self.outcome == "disabled_rights":
            result["result"]["rights"] = {"can_reply": False}
        return result

    async def send_rich_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(deepcopy(kwargs))
        self.started.set()
        if self.blocked:
            await self.release.wait()
        if self.outcome == "timeout":
            raise httpx.ReadTimeout("synthetic delivery outcome unknown")
        if self.outcome in {"api_400", "api_500"}:
            status = int(self.outcome.removeprefix("api_"))
            raise TelegramApiError(
                error_code=status,
                http_status=status,
                description="private-sensitive payload details",
            )
        result = {
            "message_id": 737,
            "chat": {"id": int(kwargs["chat_id"])},
            "business_connection_id": kwargs["business_connection_id"],
            "rich_message": {"blocks": deepcopy(kwargs["blocks"])},
        }
        if self.outcome == "wrong_chat":
            result["chat"]["id"] = 12346
        elif self.outcome == "wrong_connection":
            result["business_connection_id"] = "another-connection"
        elif self.outcome == "missing_rich":
            result.pop("rich_message")
        elif self.outcome == "empty_rich":
            result["rich_message"] = {"blocks": []}
        elif self.outcome == "missing_message_id":
            result.pop("message_id")
        elif self.outcome == "invalid_message_id":
            result["message_id"] = "not-an-integer"
        return {"ok": True, "result": result}

    async def send_plain_message(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("A Rich draft must never fall back to sendMessage")

    async def send_formatted_message(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("A Rich draft must never fall back to formatted sendMessage")


async def save_rich(sessions: Any, seed: Any) -> Any:
    async with sessions() as session:
        saved = await SaveReplyDraftService(SqlReplyDraftWriter(session)).execute(
            replace(_save_draft_command(seed, ""), text_format="rich", rich_content=rich_fixture())
        )
        await session.commit()
    assert saved.formatting is not None
    return saved


async def send(sessions: Any, telegram: RichTelegram, command: Any, *, settings: Any = None) -> Any:
    # A new SQL session/executor each time models a restarted worker; no process cache.
    async with sessions() as session:
        return await SqlTelegramReplySender(
            session=session,
            telegram=telegram,
            settings=settings or _settings().model_copy(update={"gate_rich_send_enabled": True}),
        ).send(command)


def test_converter_preserves_native_blocks_code_inline_and_order() -> None:
    source = rich_fixture()
    original = deepcopy(source)
    preview = compile_rich(source)
    assert list(preview.blocks) == expected_blocks()
    assert source == original
    assert preview.source_content == original
    assert preview.text_format == "rich" and preview.entities == ()
    assert CODE in preview.text
    assert preview.revision == 1
    # Caller edits after preview must not alter the confirmed provider blocks.
    source["blocks"][4]["text"] = "tampered"
    assert list(preview.blocks) == expected_blocks()


@pytest.mark.parametrize(
    "block",
    [
        {"type": "heading", "size": True, "text": "Heading"},
        {"type": "heading", "size": 4, "text": "Heading"},
        {"type": "paragraph", "text": "\ud800"},
        {"type": "paragraph", "text": "\x00"},
        {"type": "paragraph", "text": "💚" * 8001},
        {
            "type": "paragraph",
            "text": {"type": "url", "text": "link", "url": "javascript:alert(1)"},
        },
        {
            "type": "paragraph",
            "text": {
                "type": "url",
                "text": {"type": "url", "text": "nested", "url": "https://example.com"},
                "url": "https://example.com",
            },
        },
        {"type": "pre", "text": {"type": "bold", "text": "code"}},
        {"type": "pre", "text": "x" * 8193},
        {"type": "list", "items": [{"type": "list", "items": ["nested"]}]},
        {"type": "table", "header": ["A", "B"], "rows": [["only one cell"]]},
        {"type": "table", "header": ["A"], "rows": [["x" * 513]]},
        {"type": "image", "text": "outside sprint scope"},
        {"type": "paragraph", "text": "text", "html": "<b>unsupported</b>"},
    ],
)
def test_invalid_unsafe_or_oversized_blocks_fail_closed(block: dict[str, Any]) -> None:
    with pytest.raises(FormattingError):
        compile_rich({"schema_version": "gate.rich.v1", "blocks": [block]})


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text_format,text,content",
    [
        ("rich", "ambiguous text", rich_fixture()),
        ("rich", "", None),
        ("plain", "plain text", rich_fixture()),
        ("markdown", "**text**", rich_fixture()),
        (None, "legacy text", rich_fixture()),
    ],
)
async def test_mixed_text_rich_contract_is_rejected_before_writer(
    text_format: str | None, text: str, content: dict[str, Any] | None
) -> None:
    from test_reply_draft_application import TASK_ID, CapturingWriter, _principal

    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError) as error:
        await SaveReplyDraftService(writer).execute(
            SaveReplyDraftCommand(
                principal=_principal(scopes=frozenset({"gate:drafts:write"})),
                task_id=TASK_ID,
                text=text,
                text_format=text_format,
                rich_content=content,
            )
        )
    assert error.value.code == "invalid_arguments"
    assert writer.commands == []


@pytest.mark.anyio
async def test_six_blocks_are_saved_bound_sent_exactly_and_replayed_after_restart() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _command(seed, saved.draft_id), expected_payload_sha256=saved.formatting.payload_sha256
        )
        async with sessions() as session:
            draft = await session.get(ReplyDraft, saved.draft_id)
            assert draft is not None
            assert draft.prompt_context[FORMAT_KEY] == saved.formatting.to_dict()
            assert draft.draft_text == saved.text
        assert saved.formatting.scope == {
            "workspace_id": "mark-personal",
            "account_id": str(seed.account_id),
            "project_id": str(seed.project_id),
            "recipient_chat_id": seed.recipient_chat_id,
            "connection_id": seed.business_connection_id,
            "draft_id": str(saved.draft_id),
        }
        telegram = RichTelegram()
        first = await send(sessions, telegram, command)
        replay = await send(sessions, telegram, command)
        assert first.sent and first.status == "sent"
        assert replay.idempotent_replay
        assert replace(replay, idempotent_replay=False) == first
        assert first.payload_sha256 == saved.formatting.payload_sha256
        assert telegram.calls == [
            {
                "chat_id": seed.recipient_chat_id,
                "blocks": expected_blocks(),
                "business_connection_id": seed.business_connection_id,
            }
        ]
        assert telegram.probes == [seed.business_connection_id]
        async with sessions() as session:
            attempt = (await session.execute(select(SendAttempt))).scalar_one()
            outbound = (
                await session.execute(
                    select(Message).where(Message.source == "gate-approved-draft")
                )
            ).scalar_one()
            assert attempt.provider_message_id == "737"
            assert attempt.status == "sent" and attempt.id == first.attempt_id
            assert outbound.payload["result"]["rich_message"]["blocks"] == expected_blocks()
            assert outbound.payload["_gate_payload_sha256"] == saved.formatting.payload_sha256


@pytest.mark.anyio
async def test_concurrent_exact_replay_never_calls_provider_twice() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _command(seed, saved.draft_id), expected_payload_sha256=saved.formatting.payload_sha256
        )
        telegram = RichTelegram(blocked=True)
        first_task = asyncio.create_task(send(sessions, telegram, command))
        try:
            await asyncio.wait_for(telegram.started.wait(), timeout=5)
            replay = await asyncio.wait_for(send(sessions, telegram, command), timeout=5)
            assert replay.idempotent_replay and replay.status == "unknown" and not replay.sent
            assert replay.error_code == replay.error_detail == "rich_receipt_pending"
            assert len(telegram.calls) == 1
            telegram.release.set()
            first = await asyncio.wait_for(first_task, timeout=5)
            completed_replay = await send(sessions, telegram, command)
            assert first.sent and completed_replay.sent
            assert replay.attempt_id == first.attempt_id == completed_replay.attempt_id
            assert len(telegram.calls) == 1
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 1
        finally:
            telegram.release.set()
            if not first_task.done():
                first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [
        "timeout",
        "wrong_chat",
        "wrong_connection",
        "missing_rich",
        "empty_rich",
        "missing_message_id",
        "invalid_message_id",
    ],
)
async def test_ambiguous_rich_ack_is_unknown_and_retry_performs_no_provider_io(
    outcome: str,
) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _command(seed, saved.draft_id), expected_payload_sha256=saved.formatting.payload_sha256
        )
        telegram = RichTelegram(outcome)
        first = await send(sessions, telegram, command)
        replay = await send(sessions, telegram, command)
        assert first.status == "unknown" and not first.sent
        assert replay.idempotent_replay and replay.attempt_id == first.attempt_id
        assert len(telegram.calls) == len(telegram.probes) == 1
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.source == "gate-approved-draft")
                )
                == 0
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_consent",
        "wrong_consent",
        "text",
        "blocks",
        "source",
        "scope",
        "version",
        "hash",
        "revision",
    ],
)
async def test_changed_rich_snapshot_is_denied_before_any_provider_io(mutation: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        digest = saved.formatting.payload_sha256
        if mutation in {"missing_consent", "wrong_consent"}:
            digest = None if mutation == "missing_consent" else "0" * 64
        else:
            async with sessions() as session:
                draft = await session.get(ReplyDraft, saved.draft_id)
                assert draft is not None
                context = deepcopy(draft.prompt_context)
                snapshot = context[FORMAT_KEY]
                if mutation == "text":
                    draft.draft_text = "Другой текст"
                elif mutation == "blocks":
                    snapshot["blocks"][0]["text"] = "Другой заголовок"
                elif mutation == "source":
                    snapshot["source_content"]["blocks"][0]["text"] = "Другой источник"
                elif mutation == "scope":
                    snapshot["scope"] = {"workspace_id": "another-workspace"}
                elif mutation == "version":
                    snapshot["converter_version"] = "unreviewed-version"
                elif mutation == "hash":
                    snapshot["payload_sha256"] = "0" * 64
                elif mutation == "revision":
                    snapshot["revision"] = 2
                draft.prompt_context = context
                await session.commit()
        telegram = RichTelegram()
        command = replace(_command(seed, saved.draft_id), expected_payload_sha256=digest)
        with pytest.raises(ReplySendError):
            await send(sessions, telegram, command)
        assert telegram.calls == [] and telegram.probes == []
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field",
    ["workspace_id", "account_id", "project_id", "recipient_chat_id", "connection_id", "draft_id"],
)
async def test_valid_hash_cannot_transfer_rich_preview_to_another_scope(field: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        async with sessions() as session:
            draft = await session.get(ReplyDraft, saved.draft_id)
            assert draft is not None
            context = deepcopy(draft.prompt_context)
            snapshot = context[FORMAT_KEY]
            snapshot["scope"][field] = "other-scope-value"
            # A valid integrity digest is insufficient: actual SQL scope must match.
            canonical = json.dumps(
                {key: value for key, value in snapshot.items() if key != "payload_sha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            snapshot["payload_sha256"] = digest
            draft.prompt_context = context
            await session.commit()
        telegram = RichTelegram()
        with pytest.raises(ReplySendError) as error:
            await send(
                sessions,
                telegram,
                replace(_command(seed, saved.draft_id), expected_payload_sha256=digest),
            )
        assert error.value.code == "rich_scope_changed"
        assert telegram.probes == [] and telegram.calls == []
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
async def test_alternate_valid_rich_preview_cannot_relabel_sent_receipt() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        telegram = RichTelegram()
        first = await send(
            sessions,
            telegram,
            replace(
                _command(seed, saved.draft_id),
                expected_payload_sha256=saved.formatting.payload_sha256,
            ),
        )
        replacement_source = rich_fixture()
        replacement_source["blocks"][0]["size"] = 2
        alternate = compile_rich(replacement_source).bind(saved.formatting.scope)
        async with sessions() as session:
            draft = await session.get(ReplyDraft, saved.draft_id)
            assert draft is not None
            context = deepcopy(draft.prompt_context)
            context[FORMAT_KEY] = alternate.to_dict()
            draft.prompt_context = context
            draft.draft_text = alternate.text
            await session.commit()
        with pytest.raises(ReplySendError):
            await send(
                sessions,
                telegram,
                replace(
                    _command(seed, saved.draft_id), expected_payload_sha256=alternate.payload_sha256
                ),
            )
        assert len(telegram.calls) == len(telegram.probes) == 1
        async with sessions() as session:
            attempt = (await session.execute(select(SendAttempt))).scalar_one()
            assert attempt.id == first.attempt_id and attempt.status == "sent"


@pytest.mark.anyio
async def test_sending_reuses_frozen_blocks_without_running_converter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.application.telegram_rich as rich

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)

        def forbidden_compile(_content: Any) -> Any:
            raise AssertionError("The saved provider payload must never be regenerated at send")

        monkeypatch.setattr(rich, "compile_rich", forbidden_compile)
        telegram = RichTelegram()
        result = await send(
            sessions,
            telegram,
            replace(
                _command(seed, saved.draft_id),
                expected_payload_sha256=saved.formatting.payload_sha256,
            ),
        )
        assert result.sent
        assert telegram.calls[0]["blocks"] == expected_blocks()


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 500])
async def test_provider_failure_is_redacted_and_never_falls_back_or_retries(status: int) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _command(seed, saved.draft_id),
            expected_payload_sha256=saved.formatting.payload_sha256,
        )
        telegram = RichTelegram(f"api_{status}")
        result = await send(sessions, telegram, command)
        replay = await send(sessions, telegram, command)
        assert result.status == ("failed" if status == 400 else "unknown")
        assert not result.sent
        assert result.error_detail == result.error_code
        assert "private-sensitive" not in repr(result)
        assert replay.idempotent_replay and replay.attempt_id == result.attempt_id
        assert len(telegram.calls) == len(telegram.probes) == 1
        async with sessions() as session:
            attempt = (await session.execute(select(SendAttempt))).scalar_one()
            assert attempt.error_detail == result.error_code


@pytest.mark.anyio
async def test_finalization_failure_is_durable_unknown_with_protected_ack_and_no_resend() -> None:
    class FinalizationBrokenSender(SqlTelegramReplySender):
        async def _finalize_success(self, **kwargs: Any) -> Any:
            raise RuntimeError("private-sensitive details")

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _command(seed, saved.draft_id),
            expected_payload_sha256=saved.formatting.payload_sha256,
        )
        telegram = RichTelegram()
        async with sessions() as session:
            result = await FinalizationBrokenSender(
                session=session,
                telegram=telegram,
                settings=_settings().model_copy(update={"gate_rich_send_enabled": True}),
            ).send(command)
        assert result.status == "unknown" and not result.sent
        assert result.error_code == result.error_detail == "rich_receipt_pending"
        assert result.provider_message_id == "737"
        assert "private-sensitive" not in repr(result)
        replay = await send(sessions, telegram, command)
        assert replay.idempotent_replay
        assert replay.attempt_id == result.attempt_id and replay.provider_message_id == "737"
        assert len(telegram.calls) == len(telegram.probes) == 1
        async with sessions() as session:
            attempt = (await session.execute(select(SendAttempt))).scalar_one()
            assert attempt.status == "unknown" and attempt.provider_message_id == "737"
            assert attempt.error_detail == "rich_receipt_pending"


@pytest.mark.anyio
@pytest.mark.parametrize("disabled", ["global", "account", "rich", "rights"])
async def test_rich_respects_existing_runtime_switches_and_business_rights(disabled: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _command(seed, saved.draft_id), expected_payload_sha256=saved.formatting.payload_sha256
        )
        telegram = RichTelegram("disabled_rights" if disabled == "rights" else "sent")
        settings = _settings().model_copy(
            update={
                "gate_send_enabled": disabled != "global",
                "gate_account_ru_send_enabled": disabled != "account",
                "gate_rich_send_enabled": disabled != "rich",
            }
        )
        with pytest.raises(ReplySendError):
            await send(sessions, telegram, command, settings=settings)
        assert telegram.calls == []
        if disabled != "rights":
            assert telegram.probes == []
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0


@pytest.mark.anyio
async def test_rich_in_existing_sequence_is_explicitly_rejected_atomically() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        saved = await save_rich(sessions, seed)
        command = replace(
            _sequence_command(seed, (saved.draft_id, seed.draft_ids[0])),
            expected_payload_sha256s=(saved.formatting.payload_sha256, "0" * 64),
        )
        async with sessions() as session:
            with pytest.raises(ReplySequenceError) as error:
                await SqlReplySequenceWriter(session).queue(command)
            assert "rich" in error.value.code
            await session.rollback()
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
            for draft_id in command.draft_ids:
                draft = await session.get(ReplyDraft, draft_id)
                assert draft is not None and "reply_sequence" not in draft.prompt_context
