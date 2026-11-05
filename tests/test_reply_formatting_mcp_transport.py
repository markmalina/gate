"""Synthetic MCP-to-provider formatting contracts; no real network or database."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.api import mcp_routes, sprint9_routes, sprint10_routes, sprint17_send_draft_routes
from app.application.reply_documents import (
    PreparedReplyDocument,
    PrepareReplyDocumentCommand,
    PrepareReplyDocumentService,
)
from app.application.reply_drafts import (
    ReplyDraftError,
    SavedReplyDraft,
    SaveReplyDraftCommand,
    SaveReplyDraftService,
)
from app.application.reply_sends import ReplySendResult
from app.application.reply_sequences import QueuedReplySequence, QueuedReplySequenceItem
from app.application.telegram_formatting import (
    CAPTION_FORMAT_KEY,
    FORMAT_KEY,
    FormattingError,
    compile_text,
)
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from app.services.telegram import TelegramApiError, TelegramClient
from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings() -> Settings:
    return Settings(_env_file=None, GATE_DOCUMENT_SEND_ENABLED=True, GATE_SEND_ENABLED=True)


def _principal() -> Principal:
    return Principal(
        subject="synthetic-owner",
        auth_method="test",
        scopes=frozenset({"gate:drafts:write", "gate:messages:send"}),
        access_scope=AccessScope(
            workspace_id="synthetic-workspace",
            account_ids=frozenset({uuid4()}),
            project_ids=frozenset({uuid4()}),
        ),
    )


def _output(tool: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    assert result["result"]["isError"] is False
    content = result["result"]["structuredContent"]
    Draft202012Validator(tool["outputSchema"], format_checker=FormatChecker()).validate(content)
    return content


def _assert_main_legacy_output(tool_name: str, content: dict[str, Any]) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures/telegram_formatting_legacy_output_schemas_8e0b1672.json"
        ).read_text()
    )
    assert fixture["source_revision"] == "8e0b1672"
    validator = Draft202012Validator(fixture["schemas"][tool_name], format_checker=FormatChecker())
    validator.validate(content)
    # Prove this immutable fixture rejects newly advertised nullable fields.
    forbidden = (
        "formatting"
        if tool_name in {"gate_save_reply_draft", "gate_prepare_reply_document"}
        else "payload_sha256"
    )
    with pytest.raises(ValidationError):
        validator.validate({**content, forbidden: None})
    if tool_name == "gate_save_reply_draft":
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    **content,
                    "confirmation": {**content["confirmation"], "expected_payload_sha256": None},
                }
            )
    elif tool_name == "gate_queue_approved_reply_sequence":
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    **content,
                    "items": [{**item, "payload_sha256": None} for item in content["items"]],
                }
            )


@pytest.mark.anyio
async def test_formatted_json_transports_exact_preview_utf16_entities() -> None:
    preview = compile_text("💚 **Привет**\n[Документ](https://example.com)", "markdown")
    requests: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        await TelegramClient("synthetic-token", http_client=http).send_formatted_message(
            chat_id="synthetic-chat",
            text=preview.text,
            entities=preview.to_dict()["entities"],
            business_connection_id="synthetic-business",
            reply_to_message_id=9,
        )
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/sendMessage")
    assert json.loads(requests[0].content) == {
        "chat_id": "synthetic-chat",
        "text": "💚 Привет\nДокумент",
        "entities": [
            {"type": "bold", "offset": 3, "length": 6},
            {"type": "text_link", "offset": 10, "length": 8, "url": "https://example.com"},
        ],
        "business_connection_id": "synthetic-business",
        "reply_parameters": {"message_id": 9},
    }


@pytest.mark.anyio
async def test_caption_multipart_transports_exact_preview_entities_and_file() -> None:
    preview = compile_text("💚 **Файл**", "markdown", max_length=1024)
    requests: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 8}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        await TelegramClient("synthetic-token", http_client=http).send_business_document(
            chat_id="synthetic-chat",
            business_connection_id="synthetic-business",
            filename="synthetic.pdf",
            media_type="application/pdf",
            data=b"%PDF-1.7 fixture",
            caption=preview.text,
            caption_entities=preview.to_dict()["entities"],
        )
    assert len(requests) == 1
    request = requests[0]
    mime = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    fields = {
        part.get_param("name", header="Content-Disposition"): part.get_payload(decode=True)
        for part in mime.iter_parts()
    }
    assert set(fields) == {
        "chat_id",
        "business_connection_id",
        "caption",
        "caption_entities",
        "document",
    }
    assert fields["caption"].decode() == preview.text
    assert json.loads(fields["caption_entities"]) == [{"type": "bold", "offset": 3, "length": 4}]
    assert fields["document"] == b"%PDF-1.7 fixture"


@pytest.mark.anyio
@pytest.mark.parametrize("document", [False, True])
@pytest.mark.parametrize("failure", ["rejected", "timeout"])
async def test_formatted_provider_failure_has_one_attempt_and_no_plain_fallback(
    document: bool,
    failure: str,
) -> None:
    requests: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic ambiguous timeout", request=request)
        return httpx.Response(
            400,
            json={"ok": False, "error_code": 400, "description": "Bad Request: invalid entities"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        client = TelegramClient("synthetic-token", http_client=http)
        with pytest.raises(httpx.ReadTimeout if failure == "timeout" else TelegramApiError):
            if document:
                await client.send_business_document(
                    chat_id="synthetic-chat",
                    business_connection_id="synthetic-business",
                    filename="synthetic.pdf",
                    media_type="application/pdf",
                    data=b"%PDF-fixture",
                    caption="bold",
                    caption_entities=[{"type": "bold", "offset": 0, "length": 4}],
                )
            else:
                await client.send_formatted_message(
                    chat_id="synthetic-chat",
                    text="bold",
                    entities=[{"type": "bold", "offset": 0, "length": 4}],
                )
    assert len(requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("document", [False, True])
@pytest.mark.parametrize(
    "entity",
    [
        {"type": "bold", "offset": 1, "length": 1},
        {"type": "bold", "offset": 0, "length": 99},
        {"type": "bold", "offset": True, "length": 2},
        {"type": "unsupported", "offset": 0, "length": 2},
    ],
)
async def test_invalid_entities_fail_before_network(document: bool, entity: dict[str, Any]) -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Invalid entity reached provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        client = TelegramClient("synthetic-token", http_client=http)
        with pytest.raises(FormattingError):
            if document:
                await client.send_business_document(
                    chat_id="synthetic-chat",
                    business_connection_id="synthetic-business",
                    filename="synthetic.pdf",
                    media_type="application/pdf",
                    data=b"%PDF-fixture",
                    caption="💚",
                    caption_entities=[entity],
                )
            else:
                await client.send_formatted_message(
                    chat_id="synthetic-chat", text="💚", entities=[entity]
                )


@pytest.mark.parametrize(
    "factory,arguments,field,valid",
    [
        (
            "_gate_save_reply_draft_tool",
            {"task_id": str(uuid4()), "text": "**x**"},
            "text_format",
            "markdown",
        ),
        (
            "_gate_prepare_reply_document_tool",
            {
                "task_id": str(uuid4()),
                "filename": "fixture.pdf",
                "media_type": "application/pdf",
                "content_base64": "JVBERi0=",
                "expected_sha256": "a" * 64,
            },
            "caption_format",
            "markdown",
        ),
        (
            "_gate_send_approved_reply_tool",
            {
                "draft_id": str(uuid4()),
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-chat",
            },
            "expected_payload_sha256",
            "a" * 64,
        ),
        (
            "_gate_send_approved_document_tool",
            {
                "draft_id": str(uuid4()),
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-chat",
                "expected_document_filename": "fixture.pdf",
                "expected_document_size_bytes": 5,
                "expected_document_sha256": "b" * 64,
                "expected_document_caption": "caption",
            },
            "expected_payload_sha256",
            "a" * 64,
        ),
        (
            "_gate_queue_approved_reply_sequence_tool",
            {
                "draft_ids": [str(uuid4()), str(uuid4())],
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-chat",
                "interval_seconds": 60,
            },
            "expected_payload_sha256s",
            ["a" * 64, "b" * 64],
        ),
    ],
)
def test_actual_mcp_schemas_accept_explicit_formats_hashes_and_reject_invalid_types_or_extras(
    factory: str,
    arguments: dict[str, Any],
    field: str,
    valid: Any,
) -> None:
    tool = getattr(mcp_routes, factory)(_settings())
    validator = Draft202012Validator(tool["inputSchema"], format_checker=FormatChecker())
    if factory in {"_gate_save_reply_draft_tool", "_gate_prepare_reply_document_tool"}:
        arguments = {**arguments, "expected_context_snapshot": "dialog-v1:" + "0" * 64}
    validator.validate({**arguments, field: valid})
    for invalid in (None, 3, True, {}, [3], "unsupported"):
        with pytest.raises(ValidationError):
            validator.validate({**arguments, field: invalid})
    with pytest.raises(ValidationError):
        validator.validate({**arguments, field: valid, "parse_mode": "MarkdownV2"})


@pytest.mark.anyio
@pytest.mark.parametrize("text_format", [None, "plain", "markdown"])
async def test_mcp_save_returns_exact_compiled_preview_and_confirmation_hash(
    monkeypatch: pytest.MonkeyPatch,
    text_format: str | None,
) -> None:
    calls: list[Any] = []
    source = "  💚 **Результат**\nстрока  "
    preview = compile_text(source, text_format) if text_format else None

    class Writer:
        async def save(self, command: Any) -> SavedReplyDraft:
            calls.append(command)
            return SavedReplyDraft(
                draft_id=uuid4(),
                task_id=command.task_id,
                account_id=uuid4(),
                project_id=uuid4(),
                account_alias="ru",
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                status="draft",
                text=command.text,
                formatting=command.formatting,
            )

    monkeypatch.setattr(mcp_routes, "SqlReplyDraftWriter", lambda session: Writer())
    session = SimpleNamespace(commit=AsyncMock())
    arguments = {"task_id": str(uuid4()), "text": source}
    if text_format is not None:
        arguments["text_format"] = text_format
    result = await mcp_routes._save_reply_draft_tool_result(
        request_id=1,
        arguments=arguments,
        principal=_principal(),
        session=session,
    )
    content = _output(mcp_routes._gate_save_reply_draft_tool(_settings()), result)
    assert content["text"] == (preview.text if preview else source.strip())
    if preview:
        assert content["formatting"] == preview.to_dict()
        assert content["confirmation"]["expected_payload_sha256"] == preview.payload_sha256
    else:
        assert "formatting" not in content
        assert "expected_payload_sha256" not in content["confirmation"]
        _assert_main_legacy_output("gate_save_reply_draft", content)
    assert calls[0].formatting == preview
    session.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("caption_format", [None, "plain", "markdown"])
async def test_mcp_document_returns_exact_caption_source_entities_hash(
    monkeypatch: pytest.MonkeyPatch,
    caption_format: str | None,
) -> None:
    source = "  💚 **Документ**\n "
    preview = compile_text(source, caption_format, max_length=1024) if caption_format else None
    data = b"%PDF-1.7 synthetic document"
    commands: list[Any] = []

    class Writer:
        async def save(self, command: Any, content: bytes) -> PreparedReplyDocument:
            commands.append(command)
            assert content == data
            return PreparedReplyDocument(
                draft_id=uuid4(),
                task_id=command.task_id,
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                filename=command.filename,
                media_type=command.media_type,
                size_bytes=len(content),
                sha256=command.expected_sha256,
                caption=command.caption,
                formatting=command.formatting,
            )

    monkeypatch.setattr(mcp_routes, "SqlReplyDocumentWriter", lambda session: Writer())
    session = SimpleNamespace(commit=AsyncMock())
    arguments = {
        "task_id": str(uuid4()),
        "filename": "synthetic.pdf",
        "media_type": "application/pdf",
        "content_base64": base64.b64encode(data).decode(),
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "caption": source,
    }
    if caption_format is not None:
        arguments["caption_format"] = caption_format
    result = await mcp_routes._prepare_reply_document_tool_result(
        request_id=1,
        arguments=arguments,
        principal=_principal(),
        session=session,
        settings=_settings(),
    )
    content = _output(mcp_routes._gate_prepare_reply_document_tool(_settings()), result)
    assert content["caption"] == (preview.text if preview else source)
    if preview:
        assert content["formatting"] == preview.to_dict()
    else:
        assert "formatting" not in content
        _assert_main_legacy_output("gate_prepare_reply_document", content)
    assert commands[0].formatting == preview
    session.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("document", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_mcp_send_preserves_hash_through_application_service(
    monkeypatch: pytest.MonkeyPatch,
    document: bool,
    legacy: bool,
) -> None:
    digest = None if legacy else compile_text("**Подтверждено**", "markdown").payload_sha256
    commands: list[Any] = []

    class Sender:
        async def send(self, command: Any) -> ReplySendResult:
            commands.append(command)
            return ReplySendResult(
                attempt_id=uuid4(),
                draft_id=command.draft_id,
                task_id=uuid4(),
                account_id=uuid4(),
                project_id=uuid4(),
                account_alias="ru",
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                status="sent",
                sent=True,
                idempotent_replay=False,
                provider_message_id="8",
                payload_sha256=command.expected_payload_sha256,
                document_filename="synthetic.pdf" if document else None,
                document_media_type="application/pdf" if document else None,
                document_size_bytes=5 if document else None,
                document_sha256="b" * 64 if document else None,
            )

    monkeypatch.setattr(mcp_routes, "SqlTelegramReplySender", lambda **kwargs: Sender())
    arguments = {
        "draft_id": str(uuid4()),
        "confirmed": True,
        "expected_account": "RU",
        "expected_recipient_chat_id": "synthetic-chat",
    }
    if digest is not None:
        arguments["expected_payload_sha256"] = digest
    if document:
        arguments.update(
            expected_document_filename="synthetic.pdf",
            expected_document_size_bytes=5,
            expected_document_sha256="b" * 64,
            expected_document_caption="Подтверждено",
        )
    handler = (
        mcp_routes._send_approved_document_tool_result
        if document
        else mcp_routes._send_approved_reply_tool_result
    )
    factory = (
        mcp_routes._gate_send_approved_document_tool
        if document
        else mcp_routes._gate_send_approved_reply_tool
    )
    result = await handler(
        request_id=1,
        arguments=arguments,
        principal=_principal(),
        session=object(),
        telegram=object(),
        settings=_settings(),
    )
    content = _output(factory(_settings()), result)
    assert commands[0].expected_payload_sha256 == digest
    if legacy:
        assert "payload_sha256" not in content
        _assert_main_legacy_output(factory(_settings())["name"], content)
    else:
        assert content["payload_sha256"] == digest


@pytest.mark.anyio
@pytest.mark.parametrize("legacy", [False, True])
async def test_mcp_sequence_preserves_ordered_digests_through_application_service(
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
) -> None:
    hashes = [
        compile_text(text, "markdown").payload_sha256 for text in ("**Первый**", "💚 **Второй**")
    ]
    commands: list[Any] = []

    class Writer:
        async def queue(self, command: Any) -> QueuedReplySequence:
            commands.append(command)
            return QueuedReplySequence(
                sequence_id=uuid4(),
                task_id=uuid4(),
                account_id=uuid4(),
                project_id=uuid4(),
                account_alias="ru",
                account_label="RU",
                recipient_chat_id="synthetic-chat",
                interval_seconds=60,
                items=tuple(
                    QueuedReplySequenceItem(
                        draft_id=id_,
                        position=i,
                        scheduled_at=datetime.now(UTC),
                        payload_sha256=None if legacy else hashes[i],
                    )
                    for i, id_ in enumerate(command.draft_ids)
                ),
            )

    monkeypatch.setattr(mcp_routes, "SqlReplySequenceWriter", lambda session: Writer())
    session = SimpleNamespace(commit=AsyncMock())
    arguments = {
        "draft_ids": [str(uuid4()), str(uuid4())],
        "confirmed": True,
        "expected_account": "RU",
        "expected_recipient_chat_id": "synthetic-chat",
        "interval_seconds": 60,
    }
    if not legacy:
        arguments["expected_payload_sha256s"] = hashes
    result = await mcp_routes._queue_approved_reply_sequence_tool_result(
        request_id=1,
        arguments=arguments,
        principal=_principal(),
        session=session,
    )
    content = _output(mcp_routes._gate_queue_approved_reply_sequence_tool(_settings()), result)
    assert commands[0].expected_payload_sha256s == (None if legacy else tuple(hashes))
    if legacy:
        assert all("payload_sha256" not in item for item in content["items"])
        _assert_main_legacy_output("gate_queue_approved_reply_sequence", content)
    else:
        assert [item["payload_sha256"] for item in content["items"]] == hashes
    session.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("key", [FORMAT_KEY, CAPTION_FORMAT_KEY])
async def test_legacy_external_edit_guard_prevents_snapshot_mutation(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    draft = SimpleNamespace(prompt_context={key: {}}, draft_text="approved")
    monkeypatch.setattr(sprint10_routes, "_require_valid_draft_token", AsyncMock())
    monkeypatch.setattr(
        sprint10_routes, "_load_single_draft_for_edit", AsyncMock(return_value=(draft, object()))
    )
    request = SimpleNamespace(body=AsyncMock(side_effect=AssertionError("Blocked form body read")))
    for handler, extra in (
        (sprint10_routes.edit_draft_form, {}),
        (sprint10_routes.save_draft_form, {"request": request, "telegram": object()}),
    ):
        with pytest.raises(HTTPException) as error:
            await handler(
                draft_ref="synthetic",
                token="synthetic",
                settings=_settings(),
                session=object(),
                **extra,
            )
        assert error.value.status_code == 409
    assert draft.draft_text == "approved"
    request.body.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("key", [FORMAT_KEY, CAPTION_FORMAT_KEY])
async def test_legacy_send_guards_block_provider_for_saved_snapshots(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    draft = SimpleNamespace(prompt_context={key: {}}, status="generated")
    task = SimpleNamespace(status="needs_reply")
    telegram = SimpleNamespace(
        send_message=AsyncMock(side_effect=AssertionError("Legacy provider send"))
    )
    monkeypatch.setattr(
        sprint9_routes,
        "_find_reply_draft_rows_by_ref",
        AsyncMock(return_value=[(draft, task, object())]),
    )
    text = await sprint9_routes._send_approved_draft(
        session=object(),
        telegram=telegram,
        settings=_settings(),
        bot=object(),
        gate_event=object(),
        admin_chat_id="synthetic-admin",
        admin_business_connection_id=None,
        draft_ref="synthetic",
    )
    assert "hash confirmation" in text
    monkeypatch.setattr(
        sprint17_send_draft_routes,
        "_load_draft_rows_by_ref",
        AsyncMock(return_value=[(draft, object(), object(), object())]),
    )
    text = await sprint17_send_draft_routes._send_draft_reply(
        session=object(),
        telegram=telegram,
        settings=_settings(),
        draft_ref="synthetic",
        account_id=None,
        scope_label="synthetic",
    )
    assert "status: blocked" in text
    telegram.send_message.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool_name,handler_name,arguments,extra",
    [
        (
            "gate_save_reply_draft",
            "_save_reply_draft_tool_result",
            {"task_id": str(uuid4()), "text": "**Synthetic**", "text_format": "markdown"},
            {"parse_mode": "MarkdownV2"},
        ),
        (
            "gate_send_approved_reply",
            "_send_approved_reply_tool_result",
            {
                "draft_id": str(uuid4()),
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-chat",
                "expected_payload_sha256": "a" * 64,
            },
            {"text_format": "markdown"},
        ),
        (
            "gate_prepare_reply_document",
            "_prepare_reply_document_tool_result",
            {
                "task_id": str(uuid4()),
                "filename": "synthetic.pdf",
                "media_type": "application/pdf",
                "content_base64": "JVBERi0=",
                "expected_sha256": "a" * 64,
                "caption": "**Synthetic**",
                "caption_format": "markdown",
            },
            {"parse_mode": "MarkdownV2"},
        ),
        (
            "gate_send_approved_document",
            "_send_approved_document_tool_result",
            {
                "draft_id": str(uuid4()),
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-chat",
                "expected_document_filename": "synthetic.pdf",
                "expected_document_size_bytes": 5,
                "expected_document_sha256": "b" * 64,
                "expected_document_caption": "Synthetic",
                "expected_payload_sha256": "a" * 64,
            },
            {"caption_format": "markdown"},
        ),
        (
            "gate_queue_approved_reply_sequence",
            "_queue_approved_reply_sequence_tool_result",
            {
                "draft_ids": [str(uuid4()), str(uuid4())],
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "synthetic-chat",
                "interval_seconds": 60,
                "expected_payload_sha256s": ["a" * 64, "b" * 64],
            },
            {"unexpected_extra": "synthetic"},
        ),
    ],
)
async def test_runtime_handlers_reject_unknown_arguments_before_services(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    handler_name: str,
    arguments: dict[str, Any],
    extra: dict[str, Any],
) -> None:
    def forbidden_service(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Unknown arguments reached the application service")

    for service in (
        "SaveReplyDraftService",
        "PrepareReplyDocumentService",
        "SendApprovedReplyService",
        "QueueApprovedReplySequenceService",
        "SqlReplyDraftWriter",
        "SqlReplyDocumentWriter",
        "SqlTelegramReplySender",
        "SqlReplySequenceWriter",
    ):
        monkeypatch.setattr(mcp_routes, service, forbidden_service)
    kwargs: dict[str, Any] = {
        "request_id": "synthetic-extra",
        "arguments": {**arguments, **extra},
        "principal": _principal(),
        "session": object(),
    }
    if tool_name in {
        "gate_prepare_reply_document",
        "gate_send_approved_reply",
        "gate_send_approved_document",
    }:
        kwargs["settings"] = _settings()
    if tool_name in {"gate_send_approved_reply", "gate_send_approved_document"}:
        kwargs["telegram"] = object()
    result = await getattr(mcp_routes, handler_name)(**kwargs)
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"] == {"error": "invalid_arguments"}
    schema = mcp_routes.MCP_TOOL_OUTPUT_SCHEMAS[tool_name]
    Draft202012Validator(schema).validate(result["result"]["structuredContent"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "row_case", ["missing", "ambiguous", "legacy", FORMAT_KEY, CAPTION_FORMAT_KEY]
)
async def test_edit_link_preserves_legacy_outcomes_and_refuses_formatted_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    row_case: str,
) -> None:
    draft_id, task_id = uuid4(), uuid4()
    snapshot = {row_case: {}} if row_case in {FORMAT_KEY, CAPTION_FORMAT_KEY} else {}
    draft = SimpleNamespace(id=draft_id, status="generated", prompt_context=snapshot)
    task = SimpleNamespace(id=task_id, status="needs_reply")
    row = (draft, task, object())
    rows = [] if row_case == "missing" else [row, row] if row_case == "ambiguous" else [row]
    monkeypatch.setattr(
        sprint9_routes, "_find_reply_draft_rows_by_ref", AsyncMock(return_value=rows)
    )
    admin_reply = AsyncMock()
    monkeypatch.setattr(sprint9_routes, "_send_admin_reply", admin_reply)
    token = "synthetic-edit-token"

    def token_builder(**kwargs: Any) -> str:
        return token

    monkeypatch.setattr(sprint10_routes, "_draft_edit_token", token_builder)
    text = await sprint10_routes._send_draft_edit_link(
        request=SimpleNamespace(base_url="https://gate.example/"),
        session=object(),
        telegram=object(),
        settings=_settings(),
        bot=object(),
        gate_event=object(),
        admin_chat_id="synthetic-admin",
        admin_business_connection_id="synthetic-business",
        draft_ref="synthetic-ref",
    )
    if row_case == "missing":
        assert text == "Draft not found: synthetic-ref"
    elif row_case == "ambiguous":
        assert text == "Draft reference is ambiguous: synthetic-ref"
    elif row_case == "legacy":
        assert text == (
            f"Edit draft: {str(draft_id)[:8]}\n"
            f"https://gate.example/admin/drafts/{draft_id}/edit?token={token}\n\n"
            f"After save: /send_{str(draft_id)[:8]}\nTask: {str(task_id)[:8]}"
        )
    else:
        assert text == "Saved formatting requires Gate MCP preview/hash confirmation."
        assert "/admin/drafts/" not in text
    admin_reply.assert_awaited_once()
    assert admin_reply.await_args.kwargs["text"] == text
    assert admin_reply.await_args.kwargs["chat_id"] == "synthetic-admin"


@pytest.mark.anyio
@pytest.mark.parametrize("key", [FORMAT_KEY, CAPTION_FORMAT_KEY])
@pytest.mark.parametrize(
    "draft_status,task_status", [("generated", "needs_reply"), ("approved", "answered")]
)
async def test_effective_legacy_send_wrapper_refuses_snapshots_before_open_or_closed_path(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    draft_status: str,
    task_status: str,
) -> None:
    draft = SimpleNamespace(prompt_context={key: {}}, status=draft_status)
    task = SimpleNamespace(status=task_status)
    monkeypatch.setattr(
        sprint9_routes,
        "_find_reply_draft_rows_by_ref",
        AsyncMock(return_value=[(draft, task, object())]),
    )
    original_send = AsyncMock(side_effect=AssertionError("Snapshot entered legacy fallback"))
    admin_reply = AsyncMock(side_effect=AssertionError("Snapshot entered closed legacy path"))
    monkeypatch.setattr(sprint10_routes, "_original_send_approved_draft", original_send)
    monkeypatch.setattr(sprint9_routes, "_send_admin_reply", admin_reply)
    text = await sprint10_routes._send_approved_draft_once(
        session=object(),
        telegram=object(),
        settings=_settings(),
        bot=object(),
        gate_event=object(),
        admin_chat_id="synthetic-admin",
        admin_business_connection_id=None,
        draft_ref="synthetic-ref",
    )
    assert text == "Saved formatting requires Gate MCP preview/hash confirmation."
    original_send.assert_not_awaited()
    admin_reply.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("document", [False, True])
@pytest.mark.parametrize("invalid_mode", [None, "html", True, 17, {}, []])
async def test_explicit_invalid_or_null_format_is_rejected_before_writer(
    monkeypatch: pytest.MonkeyPatch,
    document: bool,
    invalid_mode: Any,
) -> None:
    def forbidden_writer(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Invalid explicit mode entered writer")

    monkeypatch.setattr(mcp_routes, "SqlReplyDraftWriter", forbidden_writer)
    monkeypatch.setattr(mcp_routes, "SqlReplyDocumentWriter", forbidden_writer)
    arguments = {"task_id": str(uuid4())}
    if document:
        arguments.update(
            filename="synthetic.pdf",
            media_type="application/pdf",
            content_base64="JVBERi0=",
            expected_sha256="a" * 64,
            caption="Synthetic",
            caption_format=invalid_mode,
        )
        result = await mcp_routes._prepare_reply_document_tool_result(
            request_id=1,
            arguments=arguments,
            principal=_principal(),
            session=object(),
            settings=_settings(),
        )
    else:
        arguments.update(text="Synthetic", text_format=invalid_mode)
        result = await mcp_routes._save_reply_draft_tool_result(
            request_id=1,
            arguments=arguments,
            principal=_principal(),
            session=object(),
        )
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"] == {"error": "invalid_arguments"}


@pytest.mark.anyio
async def test_omitted_reply_mode_preserves_old_strip_literal_and_character_limit() -> None:
    captured: list[SaveReplyDraftCommand] = []

    class Writer:
        async def save(self, command: SaveReplyDraftCommand) -> Any:
            captured.append(command)
            return command

    service = SaveReplyDraftService(Writer())
    injected = compile_text("**Synthetic**", "markdown")
    saved = await service.execute(
        SaveReplyDraftCommand(
            principal=_principal(),
            task_id=uuid4(),
            text=" \n💚 **unclosed _literal \t ",
            formatting=injected,
        )
    )
    assert saved.text == "💚 **unclosed _literal"
    assert saved.formatting is None and saved.text_format is None
    boundary = await service.execute(
        SaveReplyDraftCommand(
            principal=_principal(),
            task_id=uuid4(),
            text=" " + "💚" * 4096 + " ",
        )
    )
    assert len(boundary.text) == 4096 and boundary.formatting is None
    with pytest.raises(ReplyDraftError) as too_long:
        await service.execute(
            SaveReplyDraftCommand(
                principal=_principal(),
                task_id=uuid4(),
                text="x" * 4097,
            )
        )
    assert too_long.value.code == "invalid_text"
    with pytest.raises(ReplyDraftError) as strict:
        await service.execute(
            SaveReplyDraftCommand(
                principal=_principal(),
                task_id=uuid4(),
                text="💚" * 4096,
                text_format="plain",
            )
        )
    assert strict.value.code == "text_too_long"
    assert len(captured) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("document", [False, True])
async def test_explicit_null_new_send_hash_is_rejected_before_executor(
    monkeypatch: pytest.MonkeyPatch,
    document: bool,
) -> None:
    def forbidden_executor(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Explicit null hash entered executor")

    monkeypatch.setattr(mcp_routes, "SqlTelegramReplySender", forbidden_executor)
    arguments = {
        "draft_id": str(uuid4()),
        "confirmed": True,
        "expected_account": "RU",
        "expected_recipient_chat_id": "synthetic-chat",
        "expected_payload_sha256": None,
    }
    if document:
        arguments.update(
            expected_document_filename="synthetic.pdf",
            expected_document_size_bytes=5,
            expected_document_sha256="b" * 64,
            expected_document_caption="Synthetic",
        )
    handler = (
        mcp_routes._send_approved_document_tool_result
        if document
        else mcp_routes._send_approved_reply_tool_result
    )
    result = await handler(
        request_id=1,
        arguments=arguments,
        principal=_principal(),
        session=object(),
        telegram=object(),
        settings=_settings(),
    )
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"] == {"error": "invalid_arguments"}


@pytest.mark.anyio
async def test_explicit_null_new_sequence_hashes_rejected_before_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_writer(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Explicit null hashes entered writer")

    monkeypatch.setattr(mcp_routes, "SqlReplySequenceWriter", forbidden_writer)
    result = await mcp_routes._queue_approved_reply_sequence_tool_result(
        request_id=1,
        arguments={
            "draft_ids": [str(uuid4()), str(uuid4())],
            "confirmed": True,
            "expected_account": "RU",
            "expected_recipient_chat_id": "synthetic-chat",
            "interval_seconds": 60,
            "expected_payload_sha256s": None,
        },
        principal=_principal(),
        session=object(),
    )
    assert result["result"]["isError"] is True
    assert result["result"]["structuredContent"] == {"error": "invalid_arguments"}


@pytest.mark.anyio
async def test_omitted_caption_mode_preserves_old_verbatim_literal_character_limit() -> None:
    class Writer:
        async def save(self, command: PrepareReplyDocumentCommand, data: bytes) -> Any:
            assert data == b"%PDF-synthetic"
            return command

    service = PrepareReplyDocumentService(Writer())
    data = b"%PDF-synthetic"
    metadata = dict(
        principal=_principal(),
        task_id=uuid4(),
        filename="synthetic.pdf",
        media_type="application/pdf",
        content_base64=base64.b64encode(data).decode(),
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    source = " \n💚 **unclosed _literal \t "
    saved = await service.execute(
        PrepareReplyDocumentCommand(
            **metadata,
            caption=source,
            formatting=compile_text("**Synthetic**", "markdown"),
        )
    )
    assert saved.caption == source
    assert saved.formatting is None and saved.caption_format is None
    boundary = await service.execute(PrepareReplyDocumentCommand(**metadata, caption="💚" * 1024))
    assert len(boundary.caption) == 1024 and boundary.formatting is None
    with pytest.raises(ReplyDraftError) as legacy:
        await service.execute(PrepareReplyDocumentCommand(**metadata, caption="x" * 1025))
    assert legacy.value.code == "invalid_document"
    with pytest.raises(ReplyDraftError) as strict:
        await service.execute(
            PrepareReplyDocumentCommand(**metadata, caption="💚" * 1024, caption_format="plain")
        )
    assert strict.value.code == "text_too_long"
